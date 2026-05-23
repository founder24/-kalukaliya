import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/context/AuthContext';
import { Home, BookOpen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { LogoMark } from '@/components/Logo';

export default function NotFoundPage() {
  const { user } = useAuth();
  const navigate = useNavigate();

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-background px-4">
      <div className="text-center max-w-sm" data-testid="not-found-page">
        {/* Logo with orbit ring */}
        <div className="relative inline-flex mb-8">
          <LogoMark size="2xl" className="anim-float" />
          <div
            style={{
              position: 'absolute',
              inset: '-12px',
              borderRadius: '50%',
              border: '1.5px solid rgba(167,139,250,0.4)',
              animation: 'orbit 8s linear infinite',
            }}
          />
        </div>

        <h1 className="text-6xl font-bold text-foreground mb-3 shimmer-text">404</h1>
        <h2 className="text-xl font-semibold text-foreground mb-2">Page not found</h2>
        <p className="text-sm text-muted-foreground mb-8">
          The page you're looking for doesn't exist or has been moved.
        </p>

        <div className="flex items-center justify-center gap-3">
          <Button variant="outline" onClick={() => {
            if (window.history.length > 1) {
              navigate(-1);
            } else {
              navigate('/');
            }
          }}>
            Go Back
          </Button>
          <Link to={user ? '/library' : '/'}>
            <Button className="bg-primary hover:bg-primary/90 text-primary-foreground">
              {user ? (
                <><BookOpen size={14} className="mr-1.5" /> Browse</>
              ) : (
                <><Home size={14} className="mr-1.5" /> Home</>
              )}
            </Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
