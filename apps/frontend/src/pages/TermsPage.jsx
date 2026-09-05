import { PublicLayout } from '@/components/layout/PublicLayout';
import PageMeta from '@/components/seo/PageMeta';

export default function TermsPage() {
  return (
    <PublicLayout>
      <PageMeta
        title="Terms of Service"
        description="Terms of Service for Syrabit.ai — AI-powered educational browser for AHSEC, SEBA, and Degree students in Assam. Read our credit system, acceptable use, and content policies."
        url="https://syrabit.ai/terms"
        keywords="Syrabit terms, terms of service, Assam Board, AHSEC, AI tutor terms"
      />
      <div className="min-h-screen pt-8 pb-24 px-4">
        <div className="max-w-3xl mx-auto">
          <h1 className="text-3xl font-semibold text-foreground mb-2">Terms of Service</h1>
          <p className="text-muted-foreground text-sm mb-10">Last updated: January 2025</p>
          <div className="space-y-8 text-foreground/70 leading-relaxed">
            {[
              { title: '1. Acceptance of Terms', body: 'By accessing Syrabit.ai, you agree to these Terms of Service. If you do not agree, please do not use our service.' },
              { title: '2. Service Description', body: 'Syrabit.ai provides AI-powered educational assistance for AssamBoard students (AHSEC, DEGREE, and SEBA divisions). The service includes access to subject content and an AI tutor powered by Google Gemini (via Vertex AI).' },
              { title: '3. User Accounts', body: 'You are responsible for maintaining the confidentiality of your account credentials. You must provide accurate information when creating your account.' },
              { title: '4. Free Access and Advertising', body: 'Syrabit provides a daily free AI allowance that resets automatically. The service is supported by clearly labeled advertising. Attempting to bypass usage limits or interfere with advertising delivery may result in restricted access.' },
              { title: '5. Acceptable Use', body: 'You agree not to misuse the service, share your account, use it for commercial purposes without permission, or attempt to circumvent any restrictions.' },
              { title: '6. Content', body: 'AI-generated content is for educational purposes only. While we strive for accuracy, answers should be verified against official AHSEC materials.' },
              { title: '7. Privacy', body: 'Your use of the service is governed by our Privacy Policy. We collect only necessary data to provide the service.' },
              { title: '8. Termination', body: 'We reserve the right to terminate accounts that violate these terms. You may delete your account at any time from your Profile page.' },
              { title: '9. Contact', body: 'For questions about these terms, contact us at admin@syrabit.ai' },
            ].map(({ title, body }) => (
              <div key={title}>
                <h2 className="text-foreground font-semibold mb-2">{title}</h2>
                <p>{body}</p>
              </div>
            ))}
          </div>
        </div>
      </div>
    </PublicLayout>
  );
}
