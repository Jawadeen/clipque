# ClipQue Desktop

## Setup
1. Install Python 3.10+
2. Install FFmpeg and add it to PATH
3. `pip install -r requirements.txt`
4. Run: `python main.py`   or double-click `launch_clipque.bat`

## TikTok (Sandbox)
- Fill in `TIKTOK_CLIENT_KEY` and `TIKTOK_CLIENT_SECRET` in `clipque/core/oauth.py`
- Register `http://127.0.0.1:8765/callback` as the Redirect URI in TikTok Developer Portal
- Click "Connect TikTok" in the Queue / TikTok tab

## Production (after TikTok approves your app)
- In `oauth.py`: set `TIKTOK_REDIRECT_URI` to `https://clipque.vercel.app/api/tiktok-callback`
- Remove `TIKTOK_CLIENT_SECRET` from `oauth.py` (it moves to Vercel env vars)
- Update `TIKTOK_CLIENT_KEY` to your production key
