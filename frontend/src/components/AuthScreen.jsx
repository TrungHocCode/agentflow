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
      setError(requestError.message || 'Không thể xác thực tài khoản. Vui lòng thử lại.');
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
          <p className="page-eyebrow">TRỢ LÝ NGHIÊN CỨU CỦA BẠN</p>
          <h1>Biến câu hỏi lớn thành nghiên cứu rõ ràng.</h1>
          <p className="auth-story__description">
            Tổ chức việc tìm nguồn, đối chiếu thông tin và tạo báo cáo trong một quy trình bạn có thể theo dõi.
          </p>
          <div className="auth-story__steps" aria-label="Quy trình nghiên cứu">
            <span><i>01</i> Tìm nguồn phù hợp</span>
            <span><i>02</i> Tổng hợp có cấu trúc</span>
            <span><i>03</i> Nhận báo cáo dễ dùng</span>
          </div>
        </section>

        <Card className="auth-card">
          <CardHeader className="auth-card__header">
            <div className="auth-mark" aria-hidden="true"><KeyRound /></div>
            <p className="page-eyebrow">KHÔNG GIAN CÁ NHÂN</p>
            <CardTitle className="auth-card__title">
              {isRegistering ? 'Tạo tài khoản' : 'Chào mừng trở lại'}
            </CardTitle>
            <CardDescription className="auth-copy">
              {isRegistering
                ? 'Tạo tài khoản để lưu các cuộc nghiên cứu và kết quả của bạn.'
                : 'Đăng nhập để tiếp tục với các cuộc nghiên cứu và quy trình đã lưu.'}
            </CardDescription>
          </CardHeader>

          <CardContent>
            <form onSubmit={submit} className="auth-form">
              <FieldGroup>
                {isRegistering && (
                  <Field>
                    <FieldLabel htmlFor="display-name">Tên hiển thị</FieldLabel>
                    <Input
                      id="display-name"
                      autoComplete="name"
                      value={displayName}
                      onChange={event => setDisplayName(event.target.value)}
                      placeholder="Tên của bạn"
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
                  <FieldLabel htmlFor="password">Mật khẩu</FieldLabel>
                  <Input
                    id="password"
                    type="password"
                    autoComplete={isRegistering ? 'new-password' : 'current-password'}
                    value={password}
                    onChange={event => setPassword(event.target.value)}
                    placeholder={isRegistering ? 'Ít nhất 8 ký tự' : 'Nhập mật khẩu'}
                    minLength={isRegistering ? 8 : 1}
                    required
                  />
                </Field>
              </FieldGroup>

              {error && (
                <Alert variant="destructive" role="alert" className="auth-alert">
                  <AlertCircle />
                  <AlertTitle>Không thể tiếp tục</AlertTitle>
                  <AlertDescription>{error}</AlertDescription>
                </Alert>
              )}

              <Button type="submit" size="lg" className="auth-submit" disabled={submitting}>
                {submitting ? <Spinner data-icon="inline-start" /> : (isRegistering ? <UserPlus data-icon="inline-start" /> : <LogIn data-icon="inline-start" />)}
                {submitting ? 'Đang xử lý…' : (isRegistering ? 'Tạo tài khoản' : 'Đăng nhập')}
                {!submitting && <ArrowRight data-icon="inline-end" />}
              </Button>
            </form>

            <div className="auth-mode-switch">
              <span>{isRegistering ? 'Bạn đã có tài khoản?' : 'Lần đầu sử dụng AgentFlow?'}</span>
              <Button type="button" variant="link" onClick={switchMode}>
                {isRegistering ? 'Đăng nhập' : 'Tạo tài khoản'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    </main>
  );
}
