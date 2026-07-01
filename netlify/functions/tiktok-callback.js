// netlify/functions/tiktok-callback.js
//
// This is the real TikTok OAuth redirect target.
// Register this exact URL in the TikTok Developer Portal as the Redirect URI:
//
//   https://clipque.netlify.app/.netlify/functions/tiktok-callback
//
// Flow:
//   1. TikTok redirects here with ?code=...&state=...
//   2. This function exchanges the code for an access token server-side
//      (the Client Secret never touches the desktop app or the browser)
//   3. It redirects the browser to ClipQue's local listener on 127.0.0.1
//      with the token in the URL, so the desktop app can pick it up.
//   4. If anything fails, it falls back to the static /auth/tiktok/callback/
//      page so the user isn't left looking at a blank error.

const TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/";
const LOCAL_CALLBACK = "http://127.0.0.1:8765/callback";
const FALLBACK_PAGE = "https://clipque.netlify.app/auth/tiktok/callback/";

exports.handler = async (event) => {
  const params = event.queryStringParameters || {};
  const { code, error, error_description, state } = params;

  // Errors redirect straight to the local listener so the desktop app
  // can surface the real reason immediately, instead of sitting idle
  // until its timeout and showing a generic message. The static fallback
  // page is only used if the local listener itself can't be reached
  // (e.g. ClipQue wasn't running), which the browser determines on its own.
  const toLocal = (errCode) =>
    `${LOCAL_CALLBACK}?${new URLSearchParams({
      error: errCode,
      state: state || "",
    }).toString()}`;

  if (error) {
    return { statusCode: 302, headers: { Location: toLocal(error) } };
  }

  if (!code) {
    return { statusCode: 302, headers: { Location: toLocal("missing_code") } };
  }

  const clientKey = process.env.TIKTOK_CLIENT_KEY;
  const clientSecret = process.env.TIKTOK_CLIENT_SECRET;
  const redirectUri = process.env.TIKTOK_REDIRECT_URI; // must exactly match the registered URI

  if (!clientKey || !clientSecret || !redirectUri) {
    return {
      statusCode: 302,
      headers: { Location: toLocal("server_misconfigured") },
    };
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

    if (!response.ok || data.error) {
      const code = data.error || "token_exchange_failed";
      return { statusCode: 302, headers: { Location: toLocal(code) } };
    }

    // data: access_token, expires_in, open_id, refresh_token, refresh_expires_in, scope, token_type
    const forward = new URLSearchParams({
      access_token: data.access_token || "",
      refresh_token: data.refresh_token || "",
      open_id: data.open_id || "",
      expires_in: String(data.expires_in || ""),
      scope: data.scope || "",
      state: state || "",
    });

    return {
      statusCode: 302,
      headers: { Location: `${LOCAL_CALLBACK}?${forward.toString()}` },
    };
  } catch (err) {
    return {
      statusCode: 302,
      headers: { Location: toLocal("unexpected_error") },
    };
  }
};
