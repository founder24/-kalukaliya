import { PublicLayout } from '@/components/layout/PublicLayout';
import PageMeta from '@/components/seo/PageMeta';

const LAST_UPDATED = 'April 2026';
const CONTACT_EMAIL = 'privacy@syrabit.ai';

const sections = [
  {
    title: '1. Information We Collect',
    body: `We collect the following categories of personal data when you use Syrabit.ai:`,
    list: [
      'Account information — name, email address, and Google account details (if you sign in with Google).',
      'Educational profile — board, class, stream, and subjects you select during onboarding.',
      'Usage data — subjects accessed, questions asked, chat conversations, and learning activity.',
      'Technical data — device type, browser type, IP address, and approximate location (state/region level only).',
      'Payment information — if you subscribe to a paid plan, payment is processed by Razorpay. We do not store your card details directly.',
    ],
  },
  {
    title: '2. Purpose of Data Processing',
    body: `Under Section 4 of the Digital Personal Data Protection Act, 2023 (DPDP Act), we process your personal data for the following lawful purposes:`,
    list: [
      'Providing personalized educational content and AI-powered study assistance.',
      'Tracking your learning progress across subjects and chapters.',
      'Improving AI response quality and relevance through anonymized usage patterns.',
      'Maintaining and securing your account.',
      'Sending important service-related notifications (e.g., plan changes, policy updates).',
      'Compliance with applicable laws and regulations.',
    ],
  },
  {
    title: '3. Consent & Lawful Basis',
    body: `By creating an account on Syrabit.ai and checking the DPDP consent checkbox during signup, you provide explicit consent for us to collect and process your personal data as described in this policy, in accordance with Section 6 of the DPDP Act, 2023. You may withdraw consent at any time from your Profile page or by contacting us at ${CONTACT_EMAIL}. Withdrawal of consent will result in cessation of data processing within 30 days and deletion of your data as described in Section 4 below.`,
  },
  {
    title: '4. Data Storage & Retention',
    body: null,
    list: [
      'Your account data and chat conversations are stored in encrypted databases hosted on secure cloud infrastructure.',
      'AI conversations are stored server-side to provide continuity across devices and sessions.',
      'Chat history is automatically anonymized after 90 days — your name and email are removed from conversation records, leaving only anonymized academic content for quality improvement.',
      'We retain your personal data only as long as your account is active or as needed to provide services.',
      'Upon account deletion or consent withdrawal, your personal data is permanently erased within 30 days.',
      'Anonymized and aggregated data (which cannot identify you) may be retained for analytics and service improvement.',
    ],
  },
  {
    title: '5. Data Sharing & Third Parties',
    body: `We do not sell, rent, or trade your personal data. We may share data only in the following circumstances:`,
    list: [
      'AI providers — We use third-party AI model providers (such as Google Gemini via Vertex AI, Sarvam AI, Cloudflare Workers AI, Deepgram, xAI, and others) to power AI responses. Your questions are sent to these providers for processing. These providers are bound by their respective privacy policies and data processing agreements.',
      'Payment processor — Razorpay processes payments on our behalf, subject to their privacy policy.',
      'Legal obligations — We may disclose data if required by law, court order, or regulatory authority in India.',
      'We do not share identifiable personal data with advertisers or marketing companies.',
    ],
  },
  {
    title: '6. Data Security',
    body: `We implement industry-standard security measures to protect your personal data:`,
    list: [
      'All data transmitted between your browser and our servers is encrypted using TLS/HTTPS.',
      'Passwords are stored using bcrypt hashing (never in plain text).',
      'Access to production databases is restricted and logged.',
      'Security headers (HSTS, CSP, X-Content-Type-Options) are enforced on all responses.',
      'Rate limiting and bot detection protect against unauthorized automated access.',
    ],
  },
  {
    title: '7. Your Rights Under the DPDP Act, 2023',
    body: `As a Data Principal under the DPDP Act, you have the following rights:`,
    list: [
      'Right to Access (Section 11) — You can request a summary of your personal data and how it is being processed.',
      'Right to Correction (Section 12) — You can update or correct your personal information from the Profile page.',
      'Right to Erasure (Section 12) — You can delete your account and all associated personal data from the Profile page. Deletion is completed within 72 hours.',
      'Right to Grievance Redressal (Section 13) — If you have concerns about how your data is handled, contact our Data Protection Officer at ' + CONTACT_EMAIL + '.',
      'Right to Nominate (Section 14) — You may nominate another person to exercise your rights in case of your death or incapacity, by contacting us.',
    ],
  },
  {
    title: '8. Cookies & Local Storage',
    body: `We use browser local storage (not tracking cookies) to maintain your session and preferences. No third-party tracking cookies are used. Cloudflare Web Analytics is used for anonymized, aggregate page-view analytics — it is cookie-free and does not collect personally identifiable information.`,
  },
  {
    title: '9. Advertising & Opt-Out',
    body: `Syrabit.ai may display non-personalized ads to support free access. You can disable ads at any time from the Privacy section of your Profile page using the "Opt out of ads" toggle. When enabled, your browser will stop loading ad scripts on subsequent page loads. While signed in, your preference is saved to your account and synced across every device and browser you use to log in. If you are signed out, the preference is stored locally on the current device only.`,
  },
  {
    title: '10. Children\'s Privacy',
    body: `Syrabit.ai is intended for students aged 16 and above. We do not knowingly collect personal data from children under 18 without verifiable parental consent. If we discover that a child under 18 has provided personal data without appropriate consent, we will delete it promptly.`,
  },
  {
    title: '11. Cross-Border Data Transfer',
    body: `Some of our AI and cloud infrastructure providers may process data outside India. In such cases, we ensure that adequate safeguards are in place as required under Section 16 of the DPDP Act, 2023, and that such providers maintain security standards equivalent to those required under Indian law.`,
  },
  {
    title: '12. Data Breach Notification',
    body: `In the event of a personal data breach that is likely to cause harm to you, we will notify the Data Protection Board of India and affected users as required under Section 8 of the DPDP Act, 2023, without unreasonable delay.`,
  },
  {
    title: '13. Changes to This Policy',
    body: `We may update this Privacy Policy from time to time. Material changes will be communicated via email or a prominent notice on the platform. Continued use of Syrabit.ai after changes constitutes acceptance of the updated policy.`,
  },
  {
    title: '14. Grievance Officer & Contact',
    body: null,
    subsections: [
      { label: 'Grievance Officer / Data Protection Officer', value: 'Dipak Rai' },
      { label: 'Email', value: CONTACT_EMAIL },
      { label: 'Response time', value: 'Within 72 hours of receiving your request' },
      { label: 'Postal address', value: 'Syrabit.ai, Assam, India' },
    ],
    footer: 'If your grievance is not resolved satisfactorily within 30 days, you may approach the Data Protection Board of India established under Section 18 of the DPDP Act, 2023.',
  },
];

export default function PrivacyPage() {
  return (
    <PublicLayout>
      <PageMeta
        title="Privacy Policy"
        description="Privacy Policy for Syrabit.ai — how we collect, use, and protect your personal data under the Digital Personal Data Protection Act, 2023 (DPDP Act). Your rights as a student in Assam."
        url="https://syrabit.ai/privacy"
        keywords="Syrabit privacy policy, DPDP Act 2023, data protection, Assam Board, student data privacy"
      />
      <div className="min-h-screen pt-8 pb-24 px-4">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-3xl font-semibold text-foreground mb-1">Privacy Policy</h1>
          <p className="text-muted-foreground/60 text-xs mb-1">Last updated: {LAST_UPDATED}</p>
          <p className="text-muted-foreground text-sm mb-10">
            Syrabit.ai is committed to protecting your personal data in accordance with the
            Digital Personal Data Protection Act, 2023 (DPDP Act) and applicable Indian laws.
            This policy explains what data we collect, why, how we protect it, and your rights.
          </p>
          <div className="space-y-8 text-foreground/70 leading-relaxed">
            {sections.map(({ title, body, list, subsections, footer }) => (
              <div key={title}>
                <h2 className="text-foreground font-semibold mb-2">{title}</h2>
                {body && <p className="mb-2">{body}</p>}
                {list && (
                  <ul className="list-disc list-outside pl-5 space-y-1.5 text-foreground/60 text-[0.92rem]">
                    {list.map((item, i) => (
                      <li key={i}>{item}</li>
                    ))}
                  </ul>
                )}
                {subsections && (
                  <div className="space-y-1 mt-2">
                    {subsections.map(({ label, value }) => (
                      <p key={label}><span className="text-foreground/80 font-medium">{label}:</span> {value}</p>
                    ))}
                  </div>
                )}
                {footer && <p className="mt-3 text-muted-foreground text-sm">{footer}</p>}
              </div>
            ))}
          </div>
          <div className="mt-12 pt-6 border-t border-border/30 text-muted-foreground/50 text-xs">
            <p>This privacy policy is governed by the laws of India. Any disputes shall be subject to the exclusive jurisdiction of courts in Assam, India.</p>
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
