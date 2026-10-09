// El access token que entrega el Auth Service al iniciar sesión. El front lo
// guarda y lo envía a la API REST en cada petición de polling de diagnósticos.
const TOKEN_KEY = "querylens_access_token";

export function saveAccessToken(token: string): void {
  try {
    sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    // sessionStorage puede no estar disponible (modo privado, etc.)
  }
}

export function getAccessToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}
