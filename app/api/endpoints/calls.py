from asyncio import selector_events
from fastapi import responses
import asyncio
import base64
import contextlib
import json
import logging

from fastapi import (APIRouter, HTTPException, WebSocket, WebSocketDisconnect,
                     status)
from fastapi.responses import JSONResponse
from fastapi.websockets import WebSocketState
from google import genai
from google.genai import types
from pydantic import BaseModel

from app.core.base_prompt import GEMINI_SYSTEM_MESSAGE
from app.core.config import settings
from app.utils.audio_resampler import AudioResampler

logger = logging.getLogger(__name__)
router = APIRouter()
audio_resampler = AudioResampler()

class CallFlowRequest(BaseModel):
    call_id: str
    account_id: str
    from_number: str
    to_number: str

class CallRequest(BaseModel):
    from_number: str
    to_number: str

@router.post("/flow", status_code=status.HTTP_200_OK, include_in_schema=False)
async def stream_flow(payload: CallFlowRequest):
    """
    Build and return Stream flow.
    """
    if not settings.google_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GOOGLE_API_KEY not configured"
        )

    stream_flow = {
        "action": "stream",
        "ws_url": f"wss://{settings.server_domain}/api/v1/calls/media-stream",
        "chunk_size": 500,
        "sample_rate": "16k",
        "record": True
    }

    return JSONResponse(stream_flow)

@router.post("/initiate-call", status_code=status.HTTP_200_OK)
async def initiate_call(call_request: CallRequest):
    """
    Initiate a call using Teler SDK.
    """
    try:
        from app.utils.teler_client import TelerClient
        
        if not settings.google_api_key:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GOOGLE_API_KEY not configured"
            )
        
        teler_client = TelerClient(api_key=settings.teler_api_key)
        call = await teler_client.create_call(
            from_number=call_request.from_number,
            to_number=call_request.to_number,
            flow_url=f"https://{settings.server_domain}/api/v1/calls/flow",
            status_callback_url=f"https://{settings.server_domain}/api/v1/webhooks/receiver",
            record=True,
        )
        logger.info(f"Call created: {call}")
        return JSONResponse(content={"success": True, "call id": call.id})
    except Exception as e:
        logger.error(f"Failed to create call: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail="Failed to create call."
        )

@router.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle media streaming"""
    await websocket.accept()
    logger.info("WebSocket connected.")
    
    try:
        # Validate configuration
        if not settings.google_api_key:
            await websocket.close(code=1008, reason="GOOGLE_API_KEY not configured")
            return

        gemini_client = genai.Client(api_key=settings.google_api_key)
        
        # Create Gemini session directly using the client
        config = types.LiveConnectConfig(
            system_instruction=types.Content(
                parts=[types.Part(text=GEMINI_SYSTEM_MESSAGE)]
            ),
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Achird",
                    )
                ),
                language_code="en-US",
            ),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=False,
                    start_of_speech_sensitivity=types.StartSensitivity.START_SENSITIVITY_LOW,
                    end_of_speech_sensitivity=types.EndSensitivity.END_SENSITIVITY_HIGH,
                    prefix_padding_ms=150,
                    silence_duration_ms=500,
                )
            ),
        )
    
        async with gemini_client.aio.live.connect(model=settings.gemini_model, config=config) as session:
            logger.info("Successfully connected to Gemini Live session")
            
            # Audio buffering for Gemini output
            gemini_audio_chunks = []
            chunk_id = 1

            async def teler_stream():
                """Receive audio from Teler and send to Gemini"""
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        
                        if data.get("type") == "audio":
                            try:
                                # Decode and send PCM audio to Gemini (16kHz)
                                pcm_data = base64.b64decode(data["data"]["audio_b64"])
                                await session.send_realtime_input(
                                    audio=types.Blob(data=pcm_data, mime_type="audio/pcm;rate=16000")
                                )
                                logger.debug(f"Sent audio to Gemini ({len(pcm_data)} bytes)")
                            except Exception as e:
                                logger.error(f"Error sending audio to Gemini: {e}")
                                
                except WebSocketDisconnect:
                    logger.info("Teler WebSocket disconnected")
                except Exception as e:
                    logger.error(f"Error in teler stream: {e}")

            async def gemini_stream():
                """Receive audio from Gemini and send to Teler"""
                nonlocal chunk_id, gemini_audio_chunks

                async def _flush():
                    nonlocal chunk_id, gemini_audio_chunks
                    if not gemini_audio_chunks:
                        return
                    try:
                        combined_audio = b"".join(gemini_audio_chunks)

                        downsampled_data = audio_resampler.downsample(combined_audio, 24000)
                        downsampled_b64 = base64.b64encode(downsampled_data).decode('utf-8')

                        await websocket.send_json({
                            "type": "audio",
                            "audio_b64": downsampled_b64,
                            "chunk_id": chunk_id
                        })
                        logger.debug(f"Sent audio to Teler (chunk {chunk_id})")
                        chunk_id += 1
                        gemini_audio_chunks = []
                    except Exception as e:
                        logger.error(f"Error processing buffered audio: {e}")
                        gemini_audio_chunks = []
                            
                try:
                    while True:  # Keep the stream alive indefinitely
                        try:
                            async for response in session.receive():
                                sc = response.server_content
                                # Process audio data
                                if response.data is not None:
                                    gemini_audio_chunks.append(response.data)
                                    logger.debug(f"Received audio chunk ({len(response.data)} bytes)")
                                    
                                    # Send buffered audio when we have enough chunks
                                    if len(gemini_audio_chunks) >= settings.gemini_audio_chunk_count:
                                        # Combine, downsample, and send audio
                                        await _flush()
                                
                                # Handle turn completion - continue waiting for next turn
                                if sc:
                                    # User barged in — clear the buffer
                                    if getattr(sc, 'interrupted', False):
                                        logger.debug("Interruption detected, clearing gemini and call buffer")
                                        gemini_audio_chunks = []
                                        await websocket.send_json({"type": "clear"})
                                        
                                    if (getattr(sc, 'generation_complete', False)):
                                        logger.debug("Generation completed - flushing remaining buffer")
                                        await _flush()
                                    
                        except Exception as session_error:
                            logger.debug(f"Session iteration ended: {session_error}")
                            await asyncio.sleep(0.1)  # Brief pause before continuing
                            continue
                            
                except Exception as e:
                    logger.error(f"Error in gemini stream: {e}")

            # Run both streams concurrently
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(teler_stream()),
                    asyncio.create_task(gemini_stream()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            
            # Cancel remaining tasks
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            logger.info("Bridge closed")
            
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected.")
    except Exception as e:
        logger.error(f"Error in media stream: {e}")
        if websocket.client_state != WebSocketState.DISCONNECTED:
            await websocket.close()
    finally:
        logger.info("Connection closed.")
