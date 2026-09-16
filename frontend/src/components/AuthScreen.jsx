import React, { useState } from 'react';
import { KeyRound, LogIn, UserPlus } from 'lucide-react';
import { login, register } from '../services/api';

export default function AuthScreen({ onAuthenticated, initialMessage = '' }) {
  const [isRegistering, setIsRegistering] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState(initialMessage);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event) => {
    event.preventDefault();
    setSubmitting(true);
    setError('');
    try {
      const user = isRegistering
        ? await register(email, password, displayName)
        : await login(email, password);
      onAuthenticated(user);
    } catch (requestError) {
      setError(requestError.message || 'Authentication failed.');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-screen">
      <section className="glass-panel auth-card">
        <div className="auth-mark"><KeyRound size={24} /></div>
        <p className="eyebrow">AGENTFLOW RESEARCH DESK</p>
        <h1>{isRegistering ? 'Create your research desk' : 'Welcome back'}</h1>
        <p className="auth-copy">Sign in to keep your workflows, runs and research artifacts private.</p>
        <form onSubmit={submit} className="auth-form">
          {isRegistering && (
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Display name" required />
          )}
          <input value={email} onChange={(event) => setEmail(event.target.value)} type="email" placeholder="Email" required />
          <input value={password} onChange={(event) => setPassword(event.target.value)} type="password" placeholder="Password (8+ characters)" required minLength={isRegistering ? 8 : 1} />
          {error && <p className="auth-error">{error}</p>}
          <button className="btn-primary" disabled={submitting} type="submit">
            {isRegistering ? <UserPlus size={16} /> : <LogIn size={16} />}
            {submitting ? 'Đang xử lý…' : (isRegistering ? 'Tạo tài khoản' : 'Đăng nhập')}
          </button>
        </form>
        <button className="auth-toggle" onClick={() => setIsRegistering((value) => !value)}>
          {isRegistering ? 'Đã có tài khoản? Đăng nhập' : 'Chưa có tài khoản? Đăng ký'}
        </button>
      </section>
    </main>
  );
}
