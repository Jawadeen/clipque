// api/tiktok-callback.js
//
// Register this exact URL as the Redirect URI in TikTok Developer Portal:
//   https://clipque.vercel.app/api/tiktok-callback
//
// Set these in Vercel dashboard → Project Settings → Environment Variables:
//   TIKTOK_CLIENT_KEY
//   TIKTOK_CLIENT_SECRET
//   TIKTOK_REDIRECT_URI  (same URL as above, exact match)
//
// Flow:
//   1. TikTok sends the user here with ?code=...&state=...
//   2. This function exchanges the code for tokens server-side
//      (Client Secret never touches the browser or desktop app)
//   3. Redirects browser to ClipQue's local listener at 127.0.0.1:8765
//      with access_token, open_id, refresh_token, state as query params
//   4. All error paths also redirect there with an error code so the
//      desktop app surfaces the reason immediately, no timeout needed

const TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/";
const LOCAL_CALLBACK = "http://127.0.0.1:8765/callback";

export default async function handler(req, res) {
  const { code, error, state } = req.query;

  const toLocal = (errCode) => {
    const params = new URLSearchParams({ error: errCode, state: state || "" });
    return `${LOCAL_CALLBACK}?${params.toString()}`;
  };

  // TikTok sent back an error (e.g. user denied permission)
  if (error) {
    return res.redirect(302, toLocal(error));
  }

  if (!code) {
    return res.redirect(302, toLocal("missing_code"));
  }

  const clientKey = process.env.TIKTOK_CLIENT_KEY;
  const clientSecret = process.env.TIKTOK_CLIENT_SECRET;
  const redirectUri = process.env.TIKTOK_REDIRECT_URI;

  if (!clientKey || !clientSecret || !redirectUri) {
    return res.redirect(302, toLocal("server_misconfigured"));
  }

  try {
    const body = new URLSearchParams({
      client_key: clientKey,
      client_secret: clientSecret,
      code,
      grant_type: "authorization_code",
      redirect_uri: redirectUri,
    });

    const response = await fetch(TOKEN_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cache-Control": "no-cache",
      },
      body: body.toString(),
    });

    const data = await response.json();

    if (!response.ok || data.error || !data.access_token) {
      return res.redirect(302, toLocal(data.error || "token_exchange_failed"));
    }

    const forward = new URLSearchParams({
      access_token: data.access_token,
      refresh_token: data.refresh_token || "",
      open_id: data.open_id || "",
      expires_in: String(data.expires_in || ""),
      scope: data.scope || "",
      state: state || "",
    });

    return res.redirect(302, `${LOCAL_CALLBACK}?${forward.toString()}`);
  } catch (err) {
    return res.redirect(302, toLocal("unexpected_error"));
  }
}
