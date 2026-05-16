"""Centralized HTML sanitization for all user-generated content.

This module provides a single source of truth for HTML sanitization across
the entire application, ensuring consistent XSS protection.
"""
import nh3
from typing import Optional, Set, FrozenSet

# Allowed tags for markdown-rendered content
# Based on common markdown output while excluding dangerous tags like <script>, <iframe>, etc.
ALLOWED_TAGS: FrozenSet[str] = frozenset({
    'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'code', 'pre', 'ul', 'ol', 'li', 'a', 'img', 'hr',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'sup', 'sub', 'del', 'ins'
})

# Allowed attributes per tag
# Restricts potentially dangerous attributes like onclick, onload, etc.
ALLOWED_ATTRIBUTES: dict = {
    'a': {'href', 'title', 'rel', 'target'},
    'img': {'src', 'alt', 'title', 'width', 'height'},
    'td': {'colspan', 'rowspan'},
    'th': {'colspan', 'rowspan', 'scope'},
    '*': {'class', 'id'},
}

# Allowed protocols for links/images
# Only allows safe protocols, blocking javascript:, data:, vbscript:, etc.
ALLOWED_PROTOCOLS: FrozenSet[str] = frozenset({'http', 'https', 'mailto'})

# Default rel attribute for external links to prevent reverse tabnabbing
SAFE_LINK_REL = 'noopener noreferrer'


def sanitize_html(html: str, *, allow_custom_tags: Optional[Set[str]] = None) -> str:
    """
    Sanitize HTML from markdown rendering or user input.
    
    Args:
        html: Raw HTML string to sanitize
        allow_custom_tags: Optional set of additional tags to allow beyond defaults
    
    Returns:
        Sanitized HTML string safe for rendering
    
    Examples:
        >>> sanitize_html('<p>Hello <script>alert("xss")</script></p>')
        '<p>Hello </p>'
        
        >>> sanitize_html('<a href="javascript:alert(1)">Click</a>')
        '<a>Click</a>'
    """
    tags = ALLOWED_TAGS
    if allow_custom_tags:
        tags = tags.union(allow_custom_tags)
    
    return nh3.clean(
        html,
        tags=tags,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip_comments=True,
    )


def sanitize_markdown(md_content: str) -> str:
    """
    Render markdown to HTML then sanitize the output.
    
    This is the primary function for processing user-generated markdown content
    before rendering it in the UI.
    
    Args:
        md_content: Raw markdown string
    
    Returns:
        Sanitized HTML string safe for rendering
    
    Security Notes:
        - Uses nh3 (Rust-based) for fast, secure sanitization
        - Blocks all script execution vectors
        - Prevents XSS via event handlers, javascript: URLs, and data: URIs
        - Strips HTML comments that could leak sensitive information
    """
    import markdown
    
    raw_html = markdown.markdown(
        md_content,
        extensions=['fenced_code', 'tables', 'nl2br'],
        output_format='html5'
    )
    
    return sanitize_html(raw_html)


def add_safe_rel_to_links(html: str) -> str:
    """
    Add rel='noopener noreferrer' to all external links.
    
    This prevents reverse tabnabbing attacks where a linked page can
    redirect the original page to a phishing site.
    
    Args:
        html: HTML string with links
    
    Returns:
        HTML with safe rel attributes on external links
    """
    from bs4 import BeautifulSoup
    
    soup = BeautifulSoup(html, 'html.parser')
    
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        # Only add to external links (not relative or same-origin)
        if href.startswith(('http://', 'https://')):
            current_rel = link.get('rel', [])
            if isinstance(current_rel, str):
                current_rel = current_rel.split()
            
            # Add safety attributes if not present
            new_rel = set(current_rel) | {'noopener', 'noreferrer'}
            link['rel'] = list(new_rel)
        
        # Also set target="_blank" safety for external links
        if link.get('target') == '_blank' and 'noopener' not in link.get('rel', []):
            current_rel = link.get('rel', [])
            if isinstance(current_rel, str):
                current_rel = current_rel.split()
            new_rel = set(current_rel) | {'noopener', 'noreferrer'}
            link['rel'] = list(new_rel)
    
    return str(soup)


def sanitize_for_email(md_content: str) -> str:
    """
    Sanitize markdown content specifically for email templates.
    
    Email clients have different security requirements than web browsers.
    This function applies stricter sanitization suitable for email.
    
    Args:
        md_content: Raw markdown string
    
    Returns:
        Sanitized HTML safe for email rendering
    """
    # Email-safe tags are more restrictive
    email_safe_tags = frozenset({
        'p', 'br', 'strong', 'em', 'u', 'h1', 'h2', 'h3',
        'blockquote', 'ul', 'ol', 'li', 'a', 'img', 'hr',
        'table', 'tbody', 'tr', 'td', 'th'
    })
    
    import markdown
    raw_html = markdown.markdown(
        md_content,
        extensions=['fenced_code', 'tables'],
        output_format='html5'
    )
    
    # Strip potentially problematic tags for email
    return nh3.clean(
        raw_html,
        tags=email_safe_tags,
        attributes={'a': {'href', 'title'}, 'img': {'src', 'alt', 'width', 'height'}},
        protocols=ALLOWED_PROTOCOLS,
        strip_comments=True,
    )
