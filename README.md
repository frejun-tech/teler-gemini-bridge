# Teler Gemini Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/) ![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white) [![Teler Docs](https://img.shields.io/badge/Teler-Docs-0A66C2)](https://frejun.com/docs/teler/)

A reference integration between Teler and Gemini Live API, based on Media Streaming over WebSockets.

## What is Teler?

Teler is a programmable voice API by FreJun. It handles carriers, phone numbers, and real-time audio streaming so you can connect AI models directly to phone calls. → [FreJun-Teler](https://frejun.com/teler/)

## Overview

This repository connects phone calls on **Teler** to the Google Gemini Live API. It is a Python (FastAPI) server that places a call through the Teler API, receives the call's audio over Teler's [Media Streaming Bridge](https://frejun.com/docs/teler/media-streaming/bridge/), forwards it to Gemini, and streams the response audio back into the call.

Use it as a working reference: run it as-is to hear Gemini on a real phone call, then adapt the code for your own application.

## What you can build

- A phone assistant powered by Gemini's native-audio Live models
- Outbound calls where Gemini handles the full spoken conversation
- A Python base for testing Gemini Live prompts and models on real phone audio

The included `POST /api/v1/calls/initiate-call` endpoint places **outbound** calls. For **inbound** calls, point your Teler number's voice app at this server's `/api/v1/calls/flow` endpoint (see [Voice apps](https://frejun.com/docs/teler/telephony/voice-apps/)).

## How it works

```mermaid
sequenceDiagram
    participant U as Your app
    participant B as This bridge
    participant T as Teler
    participant V as Gemini
    participant C as Callee

    U->>B: POST /api/v1/calls/initiate-call
    B->>T: Create call (flow URL, status callback)
    T->>C: Rings phone
    T->>B: POST /api/v1/calls/flow
    B-->>T: Stream action (WebSocket URL)
    T->>B: Open WebSocket /api/v1/calls/media-stream
    B->>V: Open session
    C-->>T: Speaks
    T->>B: Audio chunks
    B->>V: Caller audio
    V-->>B: Agent audio
    B->>T: Audio chunks
    T-->>C: Hears the reply
    T->>B: Call events (webhook)
```

1. Your app calls `POST /api/v1/calls/initiate-call`. The bridge asks Teler to place the call, passing its own flow URL and webhook URL (both built from `SERVER_DOMAIN`).
2. When the call connects, Teler requests `/api/v1/calls/flow`, and the bridge answers with a **stream** action that points Teler to its WebSocket.
3. Teler opens a WebSocket to `/api/v1/calls/media-stream` and starts sending the caller's audio.
4. The bridge opens a session with Gemini Live API session (`GEMINI_MODEL`).
5. Audio flows both ways: caller audio is forwarded to Gemini, and Gemini's audio is sent back to Teler.
6. Audio is resampled where the rates differ: Gemini (24 kHz) → Teler (8 kHz).
7. Outgoing audio is buffered (`GEMINI_AUDIO_CHUNK_COUNT`) so Teler receives evenly sized chunks.
8. Call status events are delivered to `/api/v1/webhooks/receiver`.

More detail: [How media streaming works](https://frejun.com/docs/teler/media-streaming/how-it-works/) and [WebSocket protocol](https://frejun.com/docs/teler/media-streaming/websocket-protocol/).

## Prerequisites

- **Teler account** and API key, from the [FreJun Teler dashboard](https://platform.frejun.ai)
- A Teler phone number to call from
- Google API key with access to the Gemini Live API
- **ngrok** account and auth token (used by the Docker setup to expose the server publicly)
- **Runtime:** Docker and Docker Compose (recommended). To run without Docker: Python 3.11 (the Docker image uses `python:3.11-slim`).
- **Public HTTPS/WSS endpoint:** Teler must reach this server from the internet for the flow URL, the media WebSocket and webhooks. The Docker setup uses ngrok for this; in production, use your own domain with TLS.
- **Outbound internet access** from the server to the Teler API and to the provider's API.
- **Ports:** `8000` (app) and `4040` (ngrok inspector) must be free on the host when using Docker Compose.


## Setup

1. **Clone and configure:**
   ```bash
   git clone https://github.com/frejun-tech/teler-gemini-bridge.git
   cd teler-gemini-bridge
   cp env.example .env
   # Edit .env with your actual values
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run with Docker:**
   ```bash
   docker compose up -d --build
   ```

### Run without Docker (local development)

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

In a second terminal, expose the server with `ngrok http 8000`. Outside Docker the app cannot auto-detect the ngrok URL (it looks for the `ngrok` container), so set `SERVER_DOMAIN` in `.env` to the ngrok hostname, without `https://`, before starting the server.

### Verify it is running

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ngrok-status
```

Then place a test call with the request in **Call Initiation Example**.

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `GOOGLE_API_KEY` | Your Google API key | Required |
| `GEMINI_MODEL` | Gemini model to use | gemini-3.1-flash-live-preview |
| `GEMINI_AUDIO_CHUNK_COUNT` | Audio chunks to buffer before sending | 20 |
| `SERVER_DOMAIN` | Your ngrok domain (auto-detected) | Required |
| `TELER_API_KEY` | Your Teler API key | Required |
| `NGROK_AUTHTOKEN` | Your ngrok auth token | Required |

### Required vs optional settings

These values are taken from the code (`app/core/config.py`). Where this table and the table above differ, the code is what runs.

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELER_API_KEY` | Required | — | Teler API key, used to place calls through the Teler API |
| `GOOGLE_API_KEY` | Required | — | Google API key for Gemini |
| `GEMINI_MODEL` | Optional | `gemini-3.1-flash-live-preview` | Gemini Live model |
| `GEMINI_AUDIO_CHUNK_COUNT` | Optional | `20` | Gemini audio chunks buffered before sending to Teler |
| `NGROK_AUTHTOKEN` | Required with Docker | — | ngrok auth token, read by the `ngrok` container in `docker-compose` (not by the app itself) |
| `SERVER_DOMAIN` | Optional | auto-detected | Public hostname of this server, without `https://`. Auto-detected from ngrok when it is running; set it yourself when deploying without ngrok |
| `SERVER_HOST` | Optional | `0.0.0.0` | Interface the server binds to |
| `SERVER_PORT` | Optional | `8000` | Port the server listens on |
| `LOG_LEVEL` | Optional | `INFO` | Log verbosity |
| `FROM_NUMBER` | Optional | placeholder | Fallback caller number used by the call-initiation code |
| `TO_NUMBER` | Optional | placeholder | Fallback destination number used by the call-initiation code |

## API Endpoints

- `GET /` - Health check with server domain and provider status
- `GET /health` - Service status
- `GET /ngrok-status` - Current ngrok status and URL
- `POST /api/v1/calls/initiate-call` - Start a new call
- `POST /api/v1/calls/flow` - Get call flow configuration
- `WebSocket /api/v1/calls/media-stream` - Audio streaming
- `POST /api/v1/webhooks/receiver` - Teler webhook receiver

### Call Initiation Example

```bash
curl -X POST "http://localhost:8000/api/v1/calls/initiate-call" \
  -H "Content-Type: application/json" \
  -d '{
    "from_number": "+1234567890",
    "to_number": "+0987654321"
  }'
```

## Features

- **Gemini Live API Integration** - Direct integration with Google's Gemini Live API
- **Dynamic ngrok URL detection** - Automatically detects current ngrok domain
- **Audio buffering** - Buffers audio chunks before sending to Gemini for optimal performance
- **Sample rate conversion** - High-quality conversion between Gemini (24kHz output) and Teler (8kHz required for playback)
- **Real-time streaming** - WebSocket-based audio streaming between Teler and Gemini
- **High-quality audio processing** - Uses scipy for professional-grade audio resampling

## Troubleshooting

**`initiate-call` returns an authentication error from Teler.**
Check `TELER_API_KEY` in `.env`, then restart the container so it picks up the change. Keys are managed in the [Teler dashboard](https://platform.frejun.ai).

**The phone rings, but there is silence after answering.**
Teler could not reach the flow URL or the WebSocket. Open `/ngrok-status` and confirm a public URL is listed. If you are not using ngrok, set `SERVER_DOMAIN` to your public hostname without `https://`.

**`/ngrok-status` shows no tunnel.**
Check that `NGROK_AUTHTOKEN` is set and look at the ngrok container logs (`docker compose logs ngrok`). The free ngrok plan gives a new URL on every restart, so place calls only after the tunnel is up.

**The connection to Gemini fails or closes immediately.**
Check the Gemini credentials in `.env` (see **Required vs optional settings**) and the app logs (`docker compose logs app`) for the provider's error message.

**Audio is choppy or delayed.**
Tune `GEMINI_AUDIO_CHUNK_COUNT`. Smaller values reduce latency; larger values smooth playback.

**Running outside Docker, the app reports no server domain.**
The Python app looks for ngrok at `http://ngrok:4040`, which only exists inside Docker Compose. Set `SERVER_DOMAIN` manually.

**`Bind for 0.0.0.0:8000 failed: port is already allocated`.**
Another process is using port 8000 or 4040. Stop it, or change the host port mapping in `docker-compose`.

## Security

- **Keep secrets out of git.** Store keys only in `.env`, which is listed in `.gitignore`. Never commit real keys, and use placeholders in examples and issues.
- **Rotate keys** if you suspect a key was exposed, and use separate keys for development and production.
- **Protect the call-initiation endpoint.** `POST /api/v1/calls/initiate-call` has no authentication in this reference code, so anyone who can reach your public URL can place calls billed to your Teler account. Before exposing the server, add authentication (an API key header, for example) or restrict access by IP.
- **The flow, media and webhook endpoints are not verified** in this reference code. In production, keep the server behind your own domain and validate incoming requests.
- **Use ngrok only for development.** In production, deploy behind your own domain with TLS (`https://` and `wss://`).
- **Call recording is on by default** in this bridge. Make sure you have the consent your jurisdiction requires, or turn recording off.
- **Logs can contain call details** such as phone numbers and transcripts. Set `LOG_LEVEL` appropriately and handle logs as personal data.
- **Report security issues privately** to support@frejun.com rather than in a public GitHub issue.

## Contributing

- **Report a bug or request a feature:** open an issue at [github.com/frejun-tech/teler-gemini-bridge/issues](https://github.com/frejun-tech/teler-gemini-bridge/issues). Include steps to reproduce, the relevant logs (with keys removed) and your environment.
- **Submit a change:** fork the repository, create a branch, make your change, and open a pull request that describes what changed and how you tested it. Keep pull requests focused on one change.
- For questions about your Teler account, contact support@frejun.com.

## Related repositories

**Same integration in Node.js (TypeScript):** [teler-gemini-node-bridge](https://github.com/frejun-tech/teler-gemini-node-bridge)

**Python bridges:** [cartesia](https://github.com/frejun-tech/teler-cartesia-bridge), [deepgram](https://github.com/frejun-tech/teler-deepgram-bridge), [devnagri](https://github.com/frejun-tech/teler-devnagri-bridge), [elevenlabs](https://github.com/frejun-tech/teler-elevenlabs-bridge), [openai](https://github.com/frejun-tech/teler-openai-bridge), [ultravox](https://github.com/frejun-tech/teler-ultravox-bridge), [vapi](https://github.com/frejun-tech/teler-vapi-bridge)

**Node.js bridges:** [cartesia](https://github.com/frejun-tech/teler-cartesia-node-bridge), [deepgram](https://github.com/frejun-tech/teler-deepgram-node-bridge), [devnagri](https://github.com/frejun-tech/teler-devnagri-node-bridge), [elevenlabs](https://github.com/frejun-tech/teler-elevenlabs-node-bridge), [gemini](https://github.com/frejun-tech/teler-gemini-node-bridge), [openai](https://github.com/frejun-tech/teler-openai-node-bridge), [sarvam](https://github.com/frejun-tech/teler-sarvam-node-bridge), [ultravox](https://github.com/frejun-tech/teler-ultravox-node-bridge), [vapi](https://github.com/frejun-tech/teler-vapi-node-bridge)

**Other Teler references:** [teler-wav-bridge](https://github.com/frejun-tech/teler-wav-bridge), [teler-livekit-outbound](https://github.com/frejun-tech/teler-livekit-outbound), [teler-openai-sip-trunking](https://github.com/frejun-tech/teler-openai-sip-trunking)

**SDKs:** [teler-py](https://github.com/frejun-tech/teler-py) (Python), [teler-node](https://github.com/frejun-tech/teler-node) (Node.js)

## Documentation

- [Teler documentation](https://frejun.com/docs/teler/)
- [Quickstart](https://frejun.com/docs/teler/get-started/quickstart/)
- [Media Streaming Bridge](https://frejun.com/docs/teler/media-streaming/bridge/)
- [Gemini recipe](https://frejun.com/docs/teler/recipes/gemini/)
- [API reference](https://frejun.com/docs/teler/api-reference/)
- [FreJun Teler](https://frejun.com/teler/)
- [Teler dashboard](https://platform.frejun.ai)

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
