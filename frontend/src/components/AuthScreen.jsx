import { useState } from 'react';
import { AlertCircle, ArrowRight, KeyRound, LogIn, Sparkles, UserPlus } from 'lucide-react';
import { login, register } from '../services/api';
import { Alert, AlertDescription, AlertTitle } from './ui/alert';
import { Button } from './ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';
import { Field, FieldGroup, FieldLabel } from './ui/field';
import { Input } from './ui/input';
import { Spinner } from './ui/spinner';

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
      setError(requestError.message || 'Unable to authenticate your account. Please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = () => {
    setError('');
    setIsRegistering(value => !value);
  };

  return (
    <main className="auth-screen">
      <div className="auth-layout">
        <section className="auth-story">
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true"><Sparkles /></div>
            <div className="brand-name">AgentFlow</div>
          </div>
          <p className="page-eyebrow">YOUR RESEARCH ASSISTANT</p>
          <h1>Turn complex questions into clear research.</h1>
          <p className="auth-story__description">
            Find sources, compare evidence, and create reports in a workflow you can follow.
          </p>
          <div className="auth-story__steps" aria-label="Research workflow">
            <span><i>01</i> Find relevant sources</span>
            <span><i>02</i> Synthesize the evidence</span>
            <span><i>03</i> Get a useful report</span>
          </div>
        </section>

        <Card className="auth-card">
          <CardHeader className="auth-card__header">
            <div className="auth-mark" aria-hidden="true"><KeyRound /></div>
            <p className="page-eyebrow">YOUR WORKSPACE</p>
            <CardTitle className="auth-card__title">
              {isRegistering ? 'Create your account' : 'Welcome back'}
            </CardTitle>
            <CardDescription className="auth-copy">
              {isRegistering
                ? 'Create an account to save your research conversations and results.'
                : 'Sign in to continue with your saved research and workflows.'}
            </CardDescription>
          </CardHeader>

          <CardContent>
            <form onSubmit={submit} className="auth-form">
              <FieldGroup>
                {isRegistering && (
                  <Field>
                    <FieldLabel htmlFor="display-name">Display name</FieldLabel>
                    <Input
                      id="display-name"
                      autoComplete="name"
                      value={displayName}
                      onChange={event => setDisplayName(event.target.value)}
                      placeholder="Your name"
                      required
                    />
                  </Field>
                )}
                <Field>
                  <FieldLabel htmlFor="email">Email</FieldLabel>
                  <Input
                    id="email"
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={event => setEmail(event.target.value)}
                    placeholder="you@example.com"
                    required
                  />
                </Field>
                <Field>
                  <FieldLabel htmlFor="password">Password</FieldLabel>
                  <Input
                    id="password"
                    type="password"
                    autoComplete={isRegistering ? 'new-password' : 'current-password'}
                    value={password}
                    onChange={event => setPassword(event.target.value)}
                    placeholder={isRegistering ? 'At least 8 characters' : 'Enter your password'}
                    minLength={isRegistering ? 8 : 1}
                    required
                  />
                </Field>
              </FieldGroup>

              {error && (
                <Alert variant="destructive" role="alert" className="auth-alert">
                  <AlertCircle />
                  <AlertTitle>Unable to continue</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <Button type="submit" size="lg" className="auth-submit" disabled={submitting}>
                {submitting ? <Spinner data-icon="inline-start" /> : (isRegistering ? <UserPlus data-icon="inline-start" /> : <LogIn data-icon="inline-start" />)}
                {submitting ? 'Please wait…' : (isRegistering ? 'Create account' : 'Sign in')}
                {!submitting && <ArrowRight data-icon="inline-end" />}
              </Button>
            </form>

            <div className="auth-mode-switch">
              <span>{isRegistering ? 'Already have an account?' : 'New to AgentFlow?'}</span>
              <Button type="button" variant="link" onClick={switchMode}>
                {isRegistering ? 'Sign in' : 'Create an account'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
