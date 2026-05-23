/**
 * Staff Login Page - Phone + OTP Authentication
 * Mobile-first design for staff content managers
 */

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Smartphone, Key, ArrowRight, Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:3000';

export default function StaffLogin() {
  const navigate = useNavigate();
  const [step, setStep] = useState('phone'); // 'phone' | 'otp'
  const [phone, setPhone] = useState('');
  const [otp, setOtp] = useState('');
  const [loading, setLoading] = useState(false);
  const [debugOtp, setDebugOtp] = useState('');

  // Handle phone number submission
  const handleSendOtp = async (e) => {
    e.preventDefault();
    
    if (!isValidPhone(phone)) {
      toast.error('Please enter a valid phone number (10-15 digits)');
      return;
    }

    setLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/api/staff/login`, {
        phone: phone.trim(),
      });

      if (response.data.success) {
        setStep('otp');
        setDebugOtp(response.data.debug_otp || '');
        toast.success('OTP sent successfully!');
      } else {
        toast.error(response.data.message || 'Failed to send OTP');
      }
    } catch (error) {
      console.error('Send OTP error:', error);
      toast.error('Network error. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Handle OTP verification
  const handleVerifyOtp = async (e) => {
    e.preventDefault();
    
    if (otp.length !== 6) {
      toast.error('Please enter a valid 6-digit OTP');
      return;
    }

    setLoading(true);
    try {
      const response = await axios.post(`${API_BASE}/api/staff/verify`, {
        phone: phone.trim(),
        otp: otp.trim(),
      });

      if (response.data.success) {
        // Store token and role in localStorage
        localStorage.setItem('staffToken', response.data.token);
        localStorage.setItem('staffRole', response.data.role);
        
        toast.success('Login successful!');
        navigate('/staff/content-hub');
      } else {
        toast.error(response.data.message || 'Invalid OTP');
      }
    } catch (error) {
      console.error('Verify OTP error:', error);
      toast.error('Verification failed. Please try again.');
    } finally {
      setLoading(false);
    }
  };

  // Validate phone number format
  const isValidPhone = (num) => {
    const cleaned = num.replace(/\D/g, '');
    return cleaned.length >= 10 && cleaned.length <= 15;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-900 via-purple-900 to-pink-900 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo/Header */}
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">Syrabit.ai</h1>
          <p className="text-purple-200">Staff Management Portal</p>
        </div>

        {/* Login Card */}
        <div className="bg-white/10 backdrop-blur-lg rounded-2xl p-8 border border-white/20 shadow-2xl">
          {step === 'phone' ? (
            /* Phone Input Step */
            <form onSubmit={handleSendOtp}>
              <h2 className="text-2xl font-bold text-white mb-6">Sign In with Phone</h2>
              
              <div className="mb-6">
                <label className="block text-purple-200 text-sm font-medium mb-2">
                  Phone Number
                </label>
                <div className="relative">
                  <Smartphone className="absolute left-3 top-1/2 transform -translate-y-1/2 text-purple-300" size={20} />
                  <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    placeholder="+1 (555) 123-4567"
                    className="w-full pl-12 pr-4 py-3 bg-white/10 border border-white/20 rounded-xl text-white placeholder-purple-300/50 focus:outline-none focus:ring-2 focus:ring-purple-400 focus:border-transparent transition-all"
                    disabled={loading}
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white font-semibold py-3 px-6 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <>
                    <Loader2 size={20} className="animate-spin" />
                    Sending...
                  </>
                ) : (
                  <>
                    Send OTP
                    <ArrowRight size={20} />
                  </>
                )}
              </button>

              <p className="mt-4 text-center text-purple-200 text-sm">
                By continuing, you agree to our Terms of Service
              </p>
            </form>
          ) : (
            /* OTP Verification Step */
            <form onSubmit={handleVerifyOtp}>
              <h2 className="text-2xl font-bold text-white mb-2">Enter OTP</h2>
              <p className="text-purple-200 text-sm mb-6">
                We sent a code to {phone}
              </p>
              
              <div className="mb-6">
                <label className="block text-purple-200 text-sm font-medium mb-2">
                  One-Time Password
                </label>
                <div className="relative">
                  <Key className="absolute left-3 top-1/2 transform -translate-y-1/2 text-purple-300" size={20} />
                  <input
                    type="text"
                    value={otp}
                    onChange={(e) => setOtp(e.target.value.replace(/\D/g, '').slice(0, 6))}
                    placeholder="000000"
                    maxLength={6}
                    className="w-full pl-12 pr-4 py-3 bg-white/10 border border-white/20 rounded-xl text-white placeholder-purple-300/50 focus:outline-none focus:ring-2 focus:ring-purple-400 focus:border-transparent transition-all text-center text-2xl tracking-widest"
                    disabled={loading}
                    autoFocus
                  />
                </div>
                
                {/* Debug: Show OTP in development */}
                {debugOtp && (
                  <div className="mt-2 p-2 bg-yellow-500/20 border border-yellow-500/50 rounded-lg">
                    <p className="text-yellow-200 text-xs">
                      Debug OTP: <span className="font-mono font-bold">{debugOtp}</span>
                    </p>
                  </div>
                )}
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full bg-gradient-to-r from-purple-500 to-pink-500 hover:from-purple-600 hover:to-pink-600 text-white font-semibold py-3 px-6 rounded-xl flex items-center justify-center gap-2 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {loading ? (
                  <>
                    <Loader2 size={20} className="animate-spin" />
                    Verifying...
                  </>
                ) : (
                  'Verify & Sign In'
                )}
              </button>

              <button
                type="button"
                onClick={() => setStep('phone')}
                className="w-full mt-3 text-purple-300 hover:text-white text-sm font-medium transition-colors"
                disabled={loading}
              >
                ← Change Phone Number
              </button>

              <button
                type="button"
                onClick={handleSendOtp}
                className="w-full mt-2 text-purple-300 hover:text-white text-sm font-medium transition-colors"
                disabled={loading}
              >
                Resend OTP
              </button>
            </form>
          )}
        </div>

        {/* Footer */}
        <div className="mt-8 text-center text-purple-300/60 text-sm">
          <p>© 2024 Syrabit.ai. All rights reserved.</p>
          <p className="mt-1">For staff members only</p>
        </div>
      </div>
    </div>
  );
}
