import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { post, type Me } from "../api";
import { Brand } from "../ui";

type LoginResult = Me | { needs_password_setup: true; name: string };

export default function Login() {
  const qc = useQueryClient();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [setupFor, setSetupFor] = useState<string | null>(null);
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const signIn = (e: React.FormEvent) => {
    e.preventDefault();
    run(async () => {
      const r = await post<LoginResult>("/api/auth/login", { username, password });
      if ("needs_password_setup" in r) {
        setSetupFor(r.name);
        setPassword("");
      } else qc.setQueryData(["me"], r);
    });
  };

  const choosePassword = (e: React.FormEvent) => {
    e.preventDefault();
    if (newPw !== confirmPw) {
      setError("The two passwords don't match");
      return;
    }
    run(async () => {
      const me = await post<Me>("/api/auth/setup-password", { new_password: newPw });
      qc.setQueryData(["me"], me);
    });
  };

  return (
    <div className="login-wrap">
      {setupFor ? (
        <form className="card login" onSubmit={choosePassword}>
          <Brand compact />
          <h2 style={{ margin: 0 }}>Welcome, {setupFor}</h2>
          <p className="muted" style={{ margin: 0 }}>
            Choose a password for your account. You'll use it every time you sign in.
          </p>
          <label className="field">
            New password (6+ characters)
            <input type="password" value={newPw} onChange={(e) => setNewPw(e.target.value)} autoFocus required minLength={6} autoComplete="new-password" />
          </label>
          <label className="field">
            Type it again
            <input type="password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} required autoComplete="new-password" />
          </label>
          {error && <div className="banner error">{error}</div>}
          <button className="btn primary lg" disabled={busy || newPw.length < 6 || !confirmPw}>
            {busy ? "Saving…" : "Set password & continue"}
          </button>
          <button type="button" className="btn ghost" onClick={() => { setSetupFor(null); setNewPw(""); setConfirmPw(""); setError(null); }}>
            Back
          </button>
        </form>
      ) : (
        <form className="card login" onSubmit={signIn}>
          <Brand compact />
          <p className="muted" style={{ margin: 0 }}>
            Find duplicate photos &amp; videos and decide together what to remove.
          </p>
          <label className="field">
            Name
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} required autoFocus autoComplete="username" />
          </label>
          <label className="field">
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
          <div className="small faint" style={{ marginTop: -6 }}>First time here? Leave the password empty.</div>
          {error && <div className="banner error">{error}</div>}
          <button className="btn primary lg" disabled={busy || !username}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      )}
    </div>
  );
}
