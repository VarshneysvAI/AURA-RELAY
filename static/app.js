/**
 * AURA Relay — Autonomous AI Coworker Frontend Engine
 * Handles conversational chat, Web Speech voice input, TTS playback,
 * live inspector events (SSE), and interactive settings management.
 */

// State tracking
let isListening = false;
let recognition = null;
let sseSource = null;
let lastQuestionId = null;
let activeAudio = null;
let isMuted = localStorage.getItem('aura_muted') === 'true';
let lockedVoice = null;
let lastExecutionStatus = 'idle';

// Theme tracking
let currentTheme = document.documentElement.getAttribute('data-theme') || 'light';

document.addEventListener('DOMContentLoaded', () => {
    initVoiceEngine();
    updateMuteUI();
    updateThemeUI();
    initSpeechRecognition();
    initEventStream();
    setupInspectorToggle();
    pollState();
    loadSettingsStatus();
    fetchBackendLogs();
    // Adaptive live state polling (400ms when executing, 2000ms when idle)
    scheduleAdaptivePoll();
    // Periodically sync backend server logs
    setInterval(fetchBackendLogs, 2500);
});

let pollTimer = null;
function scheduleAdaptivePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    const isRunning = window.currentAppState && window.currentAppState.status === 'running';
    const interval = isRunning ? 400 : 2000;
    pollTimer = setTimeout(async () => {
        await pollState();
        scheduleAdaptivePoll();
    }, interval);
}

// =============================================================================
// Voice & Speech Recognition (Web Speech API)
// =============================================================================

let speechDebounceTimer = null;
let currentSpeechTranscript = '';

function startListening(customPrompt = '') {
    if (!recognition) return;
    const statusText = document.getElementById('voiceStatusText');
    const micBtn = document.getElementById('micBtn');

    // Wake up SpeechSynthesis engine if paused by browser
    if (window.speechSynthesis && window.speechSynthesis.paused) {
        window.speechSynthesis.resume();
    }

    if (isListening) {
        if (statusText && customPrompt) statusText.innerText = customPrompt;
        return;
    }

    currentSpeechTranscript = '';
    if (speechDebounceTimer) {
        clearTimeout(speechDebounceTimer);
        speechDebounceTimer = null;
    }

    // Safely abort any stale recognition instance before starting
    try {
        recognition.abort();
    } catch (e) { }

    setTimeout(() => {
        try {
            recognition.start();
            isListening = true;
            if (micBtn) micBtn.classList.add('listening');
            if (statusText) {
                statusText.innerText = customPrompt || '🎙️ Listening... speak clearly now';
            }
            updateCoworkerStatus('Listening...', 'warning');
        } catch (e) {
            console.debug('Recognition start notice:', e);
            if (e.name === 'InvalidStateError') {
                isListening = true;
                if (micBtn) micBtn.classList.add('listening');
            }
        }
    }, 120);
}

function stopListening(abort = false) {
    if (speechDebounceTimer) {
        clearTimeout(speechDebounceTimer);
        speechDebounceTimer = null;
    }
    if (recognition && isListening) {
        try {
            if (abort && typeof recognition.abort === 'function') {
                recognition.abort();
            } else {
                recognition.stop();
            }
        } catch (e) { }
    }
    isListening = false;
    currentSpeechTranscript = '';
    const micBtn = document.getElementById('micBtn');
    const statusText = document.getElementById('voiceStatusText');
    if (micBtn) micBtn.classList.remove('listening');
    if (statusText) statusText.innerText = 'Click mic or type your prompt';
}

function dispatchSpeechInput(transcript) {
    if (!transcript || !transcript.trim()) return;

    const modal = document.getElementById('questionModal');
    const modalInput = document.getElementById('modalAnswerInput');

    // Route speech directly to active question modal if open
    if (modal && !modal.classList.contains('hidden') && modalInput) {
        modalInput.value = transcript;
        const notice = document.getElementById('modalVoiceNotice');
        if (notice) notice.innerText = `Heard: "${transcript}". Submitting answer...`;
        setTimeout(() => {
            submitModalAnswer();
        }, 350);
        return;
    }

    // Route to upfront input if active
    const upfrontInput = document.getElementById('activeUpfrontInput') || document.querySelector('.upfront-answer-input');
    if (upfrontInput) {
        upfrontInput.value = transcript;
        const notice = document.getElementById('upfrontVoiceNotice');
        if (notice) notice.innerText = `Heard: "${transcript}". Proceeding...`;
        const card = upfrontInput.closest('.message-bubble');
        const submitBtn = card ? card.querySelector('.btn-upfront-submit') : null;
        if (submitBtn) {
            setTimeout(() => {
                submitBtn.click();
            }, 350);
        } else {
            sendChatMessage(transcript);
        }
        return;
    }

    // Standard chat input
    document.getElementById('chatInput').value = transcript;
    const statusText = document.getElementById('voiceStatusText');
    if (statusText) statusText.innerText = `Heard: "${transcript}"`;
    sendChatMessage(transcript);
}

function initSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const micBtn = document.getElementById('micBtn');
    const statusText = document.getElementById('voiceStatusText');

    if (!SpeechRecognition) {
        if (statusText) statusText.innerText = 'Speech recognition not supported in this browser (use Chrome or Edge).';
        return;
    }

    recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        isListening = true;
        interruptAllAudio();
        if (micBtn) micBtn.classList.add('listening');
        if (statusText) statusText.innerText = '🎙️ Listening... speak clearly now';
        updateCoworkerStatus('Listening...', 'warning');
    };

    recognition.onresult = (event) => {
        let interim = '';
        let final = '';

        for (let i = event.resultIndex; i < event.results.length; ++i) {
            const transcript = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                final += transcript + ' ';
            } else {
                interim += transcript;
            }
        }

        const heardText = (final + interim).trim();
        if (!heardText) return;

        if (final) {
            currentSpeechTranscript = (currentSpeechTranscript ? currentSpeechTranscript + ' ' : '') + final.trim();
        }
        const liveText = (currentSpeechTranscript ? currentSpeechTranscript + (interim ? ' ' + interim : '') : interim).trim();

        if (statusText) statusText.innerText = `🎙️ "${liveText}"`;

        // Live preview into active input if modal or upfront card is open
        const modal = document.getElementById('questionModal');
        const modalInput = document.getElementById('modalAnswerInput');
        if (modal && !modal.classList.contains('hidden') && modalInput) {
            modalInput.value = liveText;
        }

        const upfrontInput = document.getElementById('activeUpfrontInput') || document.querySelector('.upfront-answer-input');
        if (upfrontInput) {
            upfrontInput.value = liveText;
        }

        // Conversational debounce: Wait 1.3s of silence before concluding the user has finished speaking
        if (speechDebounceTimer) clearTimeout(speechDebounceTimer);
        speechDebounceTimer = setTimeout(() => {
            const textToSubmit = liveText || currentSpeechTranscript;
            if (!textToSubmit || !textToSubmit.trim()) return;

            stopListening(true);
            dispatchSpeechInput(textToSubmit.trim());
        }, 1300);
    };

    recognition.onerror = (event) => {
        console.warn('Speech recognition notice:', event.error);
        if (event.error !== 'no-speech') {
            stopListening();
            if (statusText) statusText.innerText = `Microphone notice: ${event.error}`;
        }
    };

    recognition.onend = () => {
        if (isListening && !speechDebounceTimer) {
            isListening = false;
            if (micBtn) micBtn.classList.remove('listening');
        }
    };

    if (micBtn) {
        micBtn.addEventListener('click', () => {
            if (isListening) {
                stopListening(true);
            } else {
                startListening();
            }
        });
    }
}

// =============================================================================
// Chat & Coworker Conversational Logic
// =============================================================================

function usePrompt(text) {
    document.getElementById('chatInput').value = text;
    sendChatMessage(text);
}

function handleChatSubmit(e) {
    e.preventDefault();
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;
    input.value = '';
    sendChatMessage(message);
}

async function sendChatMessage(message, gatheredInfo = null) {
    appendUserMessage(message);
    updateCoworkerStatus('Thinking...', 'warning');

    try {
        const resp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: message, gathered_info: gatheredInfo })
        });

        const data = await resp.json();
        if (!resp.ok) {
            appendAssistantMessage(`⚠️ Error: ${data.detail || 'Failed to process chat'}`);
            updateCoworkerStatus('Error', 'danger');
            return;
        }

        // Display assistant response
        const replyText = data.reply || 'Task accepted.';
        appendAssistantMessage(replyText, data.audio_url);

        // Check if agent needs upfront answers before starting
        if (data.type === 'ask_upfront' && data.questions && data.questions.length > 0) {
            showUpfrontQuestionsCard(data.questions, message);
            updateCoworkerStatus('Waiting for Input', 'warning');
            speakCoworker(replyText, data.audio_url, () => {
                // When coworker finishes speaking, automatically open mic for user!
                const notice = document.getElementById('upfrontVoiceNotice');
                if (notice) notice.innerHTML = '🎙️ <strong>Listening...</strong> (Speak your answer clearly now)';
                const firstInput = document.getElementById('activeUpfrontInput');
                if (firstInput) firstInput.focus();
                startListening();
            });
        } else if (data.type === 'task_started') {
            updateCoworkerStatus('Executing Task', 'active');
            speakCoworker(replyText, data.audio_url);
        } else {
            // Conversational reply or guidance: automatically open mic for seamless autonomous interaction!
            speakCoworker(replyText, data.audio_url, () => {
                startListening('🎙️ Listening... (Speak your response)');
            });
        }

    } catch (err) {
        console.error('Chat error:', err);
        appendAssistantMessage(`Connection issue: ${err.message}`);
        updateCoworkerStatus('Error', 'danger');
    }
}

function appendUserMessage(text) {
    const stream = document.getElementById('chatStream');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'chat-message user-message';
    msgDiv.innerHTML = `
        <div class="avatar user-avatar"><span>U</span></div>
        <div class="message-bubble">
            <div class="message-text">${escapeHtml(text)}</div>
        </div>
    `;
    stream.appendChild(msgDiv);
    stream.scrollTop = stream.scrollHeight;
}

function appendAssistantMessage(text, audioUrl = null) {
    const stream = document.getElementById('chatStream');
    const msgDiv = document.createElement('div');
    msgDiv.className = 'chat-message assistant-message';

    msgDiv.innerHTML = `
        <div class="avatar aura-avatar"><span>A</span></div>
        <div class="message-bubble">
            <div class="message-header">
                <span class="sender-name">AURA Coworker</span>
                <button class="btn-audio-replay" title="Replay voice">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon>
                        <path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path>
                    </svg>
                </button>
            </div>
            <div class="message-text">${formatMessageText(text)}</div>
        </div>
    `;
    const replayBtn = msgDiv.querySelector('.btn-audio-replay');
    if (replayBtn) {
        replayBtn.addEventListener('click', () => {
            handleUserReplay(audioUrl, text);
        });
    }
    stream.appendChild(msgDiv);
    stream.scrollTop = stream.scrollHeight;
}

function showUpfrontQuestionsCard(questions, originalGoal) {
    const stream = document.getElementById('chatStream');
    const card = document.createElement('div');
    card.className = 'chat-message assistant-message';

    // Strictly display ONE question at a time
    const singleQuestion = questions && questions.length > 0 ? questions[0] : "Please provide more details:";

    card.innerHTML = `
        <div class="avatar aura-avatar"><span>A</span></div>
        <div class="message-bubble" style="border-color: rgba(245, 158, 11, 0.5); width: 100%;">
            <div class="message-header">
                <span class="sender-name" style="color: #f59e0b;">Quick Clarification</span>
            </div>
            <div class="message-text">
                <p style="margin-bottom: 10px; font-weight: 500; color: #f1f5f9;">${escapeHtml(singleQuestion)}</p>
                <div style="position: relative; width: 100%; margin-bottom: 8px;">
                    <input type="text" id="activeUpfrontInput" class="upfront-answer-input" data-question="${escapeAttr(singleQuestion)}" placeholder="Speak your answer or type here..." style="width: 100%; padding-right: 42px;" />
                    <button type="button" onclick="startListening()" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; font-size: 16px; cursor: pointer;" title="Speak answer">🎙️</button>
                </div>
                <div id="upfrontVoiceNotice" style="font-size: 12px; color: #a5b4fc; min-height: 18px; margin-bottom: 10px;">
                    🔊 Coworker is asking...
                </div>
                <div style="display: flex; justify-content: flex-end;">
                    <button class="btn btn-primary btn-sm btn-upfront-submit" onclick="submitUpfrontAnswers('${escapeAttr(originalGoal)}', this)">Confirm &amp; Proceed</button>
                </div>
            </div>
        </div>
    `;
    stream.appendChild(card);
    stream.scrollTop = stream.scrollHeight;

    const input = card.querySelector('#activeUpfrontInput');
    if (input) {
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const btn = card.querySelector('.btn-upfront-submit');
                if (btn) btn.click();
            }
        });
    }
}

function submitUpfrontAnswers(originalGoal, btnEl) {
    stopListening(true);
    const card = btnEl.closest('.message-bubble');
    const input = card.querySelector('#activeUpfrontInput') || card.querySelector('.upfront-answer-input');
    const answer = input ? input.value.trim() : '';
    if (!answer) return;

    btnEl.disabled = true;
    btnEl.innerText = 'Starting...';
    sendChatMessage(answer, { "user_preference": answer });
}

// =============================================================================
// Voice Audio Engine, Mute Control & Playback
// =============================================================================

function initVoiceEngine() {
    function pickVoice() {
        if (!window.speechSynthesis) return;
        const voices = window.speechSynthesis.getVoices();
        if (!voices || voices.length === 0) return;

        // Preferred order for natural, clear, consistent coworker speech
        const preferredNames = [
            'Microsoft Jenny Online (Natural)',
            'Microsoft Aria Online (Natural)',
            'Google US English',
            'Microsoft Zira',
            'Samantha',
            'en-US'
        ];

        for (const pref of preferredNames) {
            const found = voices.find(v => v.name.includes(pref) || (pref === 'en-US' && v.lang === 'en-US'));
            if (found) {
                lockedVoice = found;
                break;
            }
        }
        if (!lockedVoice) {
            lockedVoice = voices.find(v => v.lang && v.lang.startsWith('en')) || voices[0];
        }
    }

    if (window.speechSynthesis) {
        pickVoice();
        window.speechSynthesis.onvoiceschanged = pickVoice;
    }

    // Unlock browser AudioContext and SpeechSynthesis on first user interaction
    function unlockAudio() {
        if (window.speechSynthesis) {
            window.speechSynthesis.resume();
        }
        const player = document.getElementById('auraAudioPlayer');
        if (player) {
            player.play().then(() => player.pause()).catch(() => {});
        }
    }
    document.addEventListener('click', unlockAudio, { once: true, passive: true });
    document.addEventListener('keydown', unlockAudio, { once: true, passive: true });
}

function toggleMute() {
    isMuted = !isMuted;
    localStorage.setItem('aura_muted', isMuted ? 'true' : 'false');
    updateMuteUI();
    if (isMuted) {
        interruptAllAudio();
    }
}

function updateMuteUI() {
    const btnText = document.getElementById('muteToggleText');
    const soundIcon = document.getElementById('soundIcon');
    const muteIcon = document.getElementById('muteIcon');
    const muteBtn = document.getElementById('muteToggleBtn');

    if (isMuted) {
        if (btnText) btnText.innerText = 'Muted';
        if (soundIcon) soundIcon.style.display = 'none';
        if (muteIcon) muteIcon.style.display = 'inline-block';
        if (muteBtn) {
            muteBtn.classList.add('muted');
            muteBtn.title = 'Sound is Muted (Click to Unmute)';
        }
    } else {
        if (btnText) btnText.innerText = 'Sound On';
        if (soundIcon) soundIcon.style.display = 'inline-block';
        if (muteIcon) muteIcon.style.display = 'none';
        if (muteBtn) {
            muteBtn.classList.remove('muted');
            muteBtn.title = 'Sound is On (Click to Mute)';
        }
    }
}

function stopLocalAudio() {
    const player = document.getElementById('auraAudioPlayer');
    if (player) {
        player.pause();
        player.currentTime = 0;
        player.onended = null;
    }
    if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
    }
}

function interruptAllAudio() {
    stopLocalAudio();
    // Only notify backend RealtimeTTS when explicitly interrupted by user action
    try {
        fetch('/api/tts/stop', { method: 'POST' }).catch(() => { });
    } catch (e) { }
}

function speakCoworker(text, audioUrl = null, onEndedCallback = null) {
    // If no custom callback is provided, automatically open mic hands-free so user can immediately speak without keyboard/mouse
    const effectiveCallback = onEndedCallback || (() => {
        startListening('🎙️ Listening... (Speak hands-free)');
    });

    if (isMuted) {
        effectiveCallback();
        return;
    }

    // Stop previous local playback without halting backend RealtimeTTS
    stopLocalAudio();

    // STRICT HALF-DUPLEX: Turn off microphone while AURA is speaking!
    stopListening(true);
    updateCoworkerStatus('AURA Speaking...', 'active');
    const micBtn = document.getElementById('micBtn');
    if (micBtn) {
        micBtn.classList.add('speaking-disabled');
    }

    let endedFired = false;
    const callEnded = () => {
        if (!endedFired) {
            endedFired = true;
            if (micBtn) {
                micBtn.classList.remove('speaking-disabled');
            }
            effectiveCallback();
        }
    };

    // If an audio file URL is available (e.g. from server synthesis), play via audio element
    if (audioUrl && audioUrl !== 'realtime') {
        const player = document.getElementById('auraAudioPlayer');
        if (player) {
            player.onended = callEnded;
            player.onerror = () => {
                player.onended = null;
                speakWithBrowser(text, callEnded);
            };
            player.src = audioUrl;
            player.play().catch(e => {
                speakWithBrowser(text, callEnded);
            });
            return;
        }
    }

    // Always use browser SpeechSynthesis for reliable, crystal-clear, zero-lag in-tab voice
    speakWithBrowser(text, callEnded);
}

function speakWithBrowser(text, onEndedCallback = null) {
    if (isMuted || !window.speechSynthesis) {
        if (onEndedCallback) onEndedCallback();
        return;
    }

    // Cancel and resume immediately (resolves Chrome silent audio stall bug)
    window.speechSynthesis.cancel();
    window.speechSynthesis.resume();

    // Clear orphaned active utterances
    window._activeUtterances = [];

    // Clean text: strip markdown tables, code blocks, URLs, and formatting
    let clean = text
        .replace(/```[\s\S]*?```/g, ' Code snippet provided in chat. ')
        .replace(/\|[^\n]+\|/g, ' ')
        .replace(/[-|:]{3,}/g, ' ')
        .replace(/<[^>]*>/g, '')
        .replace(/https?:\/\/[^\s]+/g, '')
        .replace(/\[([^\]]+)\]\([^\)]+\)/g, '$1')
        .replace(/[*#_`~]/g, '')
        .replace(/\s+/g, ' ')
        .trim();

    if (!clean) {
        if (onEndedCallback) onEndedCallback();
        return;
    }

    // Split into natural sentences so Chrome doesn't stall on long single utterances
    const sentenceRegex = /[^.!?]+[.!?]+|[^.!?]+$/g;
    const matched = clean.match(sentenceRegex) || [clean];
    const chunks = matched.map(s => s.trim()).filter(Boolean);

    if (chunks.length === 0) {
        if (onEndedCallback) onEndedCallback();
        return;
    }

    let currentIndex = 0;
    let hasEnded = false;
    let resumeInterval = null;

    const finishAll = () => {
        if (hasEnded) return;
        hasEnded = true;
        if (resumeInterval) clearInterval(resumeInterval);
        window._activeUtterances = [];
        if (onEndedCallback) onEndedCallback();
    };

    const speakNextChunk = () => {
        if (hasEnded) return;
        if (currentIndex >= chunks.length) {
            finishAll();
            return;
        }

        const chunkText = chunks[currentIndex++];
        const utterance = new SpeechSynthesisUtterance(chunkText);
        utterance.rate = 1.05;
        utterance.pitch = 1.0;

        if (lockedVoice) {
            utterance.voice = lockedVoice;
        } else if (window.speechSynthesis.getVoices().length > 0) {
            const vs = window.speechSynthesis.getVoices();
            utterance.voice = vs.find(v => v.lang && v.lang.startsWith('en')) || vs[0];
        }

        window._activeUtterances.push(utterance);

        utterance.onend = () => {
            speakNextChunk();
        };
        utterance.onerror = (e) => {
            console.warn('TTS utterance notice:', e);
            speakNextChunk();
        };

        window.speechSynthesis.speak(utterance);
        window.speechSynthesis.resume();
    };

    // Chromium timer bug workaround: keep speech engine alive while speaking
    resumeInterval = setInterval(() => {
        if (!hasEnded && window.speechSynthesis.speaking) {
            window.speechSynthesis.resume();
        } else if (!hasEnded && currentIndex >= chunks.length && !window.speechSynthesis.speaking) {
            finishAll();
        }
    }, 2500);

    // Dynamic safety fallback timeout based on total text length
    const fallbackTimeoutMs = Math.max(10000, clean.length * 130 + 5000);
    setTimeout(() => {
        if (!hasEnded && !window.speechSynthesis.speaking) {
            finishAll();
        }
    }, fallbackTimeoutMs);

    speakNextChunk();
}

function handleUserReplay(audioUrl, text) {
    if (isMuted) {
        toggleMute(); // User clicked replay explicitly, unmute
    }
    if (audioUrl && audioUrl !== 'realtime') {
        const player = document.getElementById('auraAudioPlayer');
        if (player) {
            player.currentTime = 0;
            player.play().catch(() => speakWithBrowser(text));
            return;
        }
    }
    speakWithBrowser(text);
}

function replayWelcomeAudio() {
    if (isMuted) {
        toggleMute(); // User explicitly clicked replay, unmute
    }
    speakCoworker("Hey there! I am AURA, your autonomous web coworker. Tap the microphone to talk or type your request below!");
}

// =============================================================================
// Agent Inspector & Realtime Timeline (SSE)
// =============================================================================

function toggleInspector() {
    const inspector = document.getElementById('agentInspector');
    const toggleBtn = document.getElementById('togglePanelBtn');
    if (!inspector) return;
    const isCollapsed = inspector.classList.toggle('collapsed');
    if (toggleBtn) {
        if (isCollapsed) {
            toggleBtn.classList.remove('active');
        } else {
            toggleBtn.classList.add('active');
        }
    }
}

function setupInspectorToggle() {
    // Handled via onclick in HTML
}

function initEventStream() {
    sseSource = new EventSource('/api/events');

    sseSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            handleStateUpdate(data);
        } catch (e) {
            console.debug('SSE parse error:', e);
        }
    };

    sseSource.onopen = () => {
        if (window.currentAppState && window.currentAppState.status === 'running') {
            updateCoworkerStatus('Executing Task...', 'active');
        } else if (!window.currentAppState || window.currentAppState.status === 'idle') {
            updateCoworkerStatus('Coworker Ready', 'active');
        }
    };

    sseSource.onerror = () => {
        console.debug('SSE reconnecting...');
        updateCoworkerStatus('Reconnecting...', 'warning');
    };
}

// =============================================================================
// Live Browser Viewport Rendering (Double-buffered & Zero-latency)
// =============================================================================

let currentRenderedScreenshotUrl = '';
let isLiveStreamActive = false;

function activateLiveStream() {
    const liveImg = document.getElementById('liveScreenshot');
    const noPreview = document.getElementById('noPreview');
    const streamUrl = '/api/browser/stream';

    if (!liveImg) return;
    if (isLiveStreamActive && liveImg.src && liveImg.src.includes('/api/browser/stream')) {
        return;
    }

    isLiveStreamActive = true;
    liveImg.onload = () => {
        liveImg.style.display = 'block';
        if (noPreview) noPreview.style.display = 'none';
    };
    liveImg.onerror = () => {
        isLiveStreamActive = false;
    };
    liveImg.src = `${streamUrl}?t=${Date.now()}`;
    liveImg.style.display = 'block';
    if (noPreview) noPreview.style.display = 'none';
}

function deactivateLiveStream(lastScreenshotPath = null) {
    isLiveStreamActive = false;
    if (lastScreenshotPath) {
        updateRenderedScreenshot(lastScreenshotPath, true);
    }
}

function updateRenderedScreenshot(rawUrl, force = false) {
    const liveImg = document.getElementById('liveScreenshot');
    const noPreview = document.getElementById('noPreview');
    const lightboxModal = document.getElementById('viewportModal');
    const lightboxImg = document.getElementById('lightboxScreenshot');
    const placeholder = document.getElementById('lightboxPlaceholder');
    const downloadBtn = document.getElementById('downloadScreenshotBtn');

    if (!rawUrl) {
        if (liveImg) liveImg.style.display = 'none';
        if (noPreview) noPreview.style.display = 'block';
        if (lightboxImg) lightboxImg.style.display = 'none';
        if (placeholder) placeholder.style.display = 'flex';
        currentRenderedScreenshotUrl = '';
        return;
    }

    // Compare canonical path without cache-busters
    const canonicalPath = rawUrl.split('?')[0];
    const currentCanonical = currentRenderedScreenshotUrl.split('?')[0];

    // If already displaying this exact screenshot and not forced, return immediately (zero flicker/refetch)
    if (canonicalPath === currentCanonical && !force) {
        return;
    }

    const finalUrl = force ? `${canonicalPath}?t=${Date.now()}` : canonicalPath;

    // Double-buffered offscreen preloading: keeps current frame visible until new frame is fully ready
    const preloader = new Image();
    preloader.onload = () => {
        currentRenderedScreenshotUrl = finalUrl;
        if (liveImg) {
            liveImg.src = finalUrl;
            liveImg.style.display = 'block';
        }
        if (noPreview) noPreview.style.display = 'none';

        if (lightboxModal && !lightboxModal.classList.contains('hidden')) {
            if (lightboxImg) {
                lightboxImg.src = finalUrl;
                lightboxImg.style.display = 'block';
            }
            if (placeholder) placeholder.style.display = 'none';
            if (downloadBtn) downloadBtn.href = finalUrl;
        }
    };

    preloader.onerror = () => {
        if (!liveImg || !liveImg.src) {
            if (liveImg) liveImg.style.display = 'none';
            if (noPreview) noPreview.style.display = 'block';
        }
    };

    preloader.src = finalUrl;
}

function handleStateUpdate(data) {
    if (!data) return;
    // Normalize data: whether from /api/state or SSE event
    let state = data;
    if (data.state) {
        state = { ...data.state };
        if (data.type && data.message) {
            state.events = [data];
        }
    }

    // Update step and reason in inspector
    const stepEl = document.getElementById('inspectorStep');
    const reasonEl = document.getElementById('inspectorReason');
    const stopBtn = document.getElementById('stopAgentBtn');
    const screenshotImg = document.getElementById('liveScreenshot');
    const noPreview = document.getElementById('noPreview');

    if (stepEl && state.current_step) stepEl.innerText = state.current_step;
    if (reasonEl && (state.reason || state.next_step)) reasonEl.innerText = state.reason || state.next_step;

    // Update button states
    const pauseBtn = document.getElementById('pauseAgentBtn');
    const resumeBtn = document.getElementById('resumeAgentBtn');

    if (state.status !== undefined) {
        if (stopBtn) stopBtn.disabled = (state.status !== 'running' && state.status !== 'paused');

        if (pauseBtn && resumeBtn) {
            if (state.status === 'running') {
                pauseBtn.style.display = 'inline-flex';
                pauseBtn.disabled = false;
                resumeBtn.style.display = 'none';
            } else if (state.status === 'paused') {
                pauseBtn.style.display = 'none';
                resumeBtn.style.display = 'inline-flex';
                resumeBtn.disabled = false;
            } else {
                pauseBtn.style.display = 'inline-flex';
                pauseBtn.disabled = true;
                resumeBtn.style.display = 'none';
            }
        }
    }

    window.currentAppState = state;

    // Update coworker status pill and handle task completion/announcements
    if (state.status === 'running') {
        lastExecutionStatus = 'running';
        updateCoworkerStatus('Executing Task...', 'active');
    } else if (state.status === 'done') {
        updateCoworkerStatus('Task Completed', 'active');
        if (lastExecutionStatus !== 'done') {
            lastExecutionStatus = 'done';
            const finalResult = state.result || 'Task completed successfully.';
            appendAssistantMessage(`🎉 **Task Complete:**\n\n${finalResult}\n\n*Would you like me to do anything else for you?*`);
            
            // Clean spoken summary for voice synthesizer
            let spokenSummary = finalResult
                .replace(/\[(.*?)\]\(https?:\/\/[^\s\)]+\)/g, '$1')
                .replace(/#{1,6}\s+/g, '')
                .replace(/[*_`]/g, '')
                .replace(/\n+/g, ' ')
                .trim();
            if (spokenSummary.length > 180) {
                spokenSummary = spokenSummary.substring(0, 180) + '... Full details and links are in our chat.';
            }
            speakCoworker(`Task completed! ${spokenSummary}. Would you like me to do anything else for you?`, null, () => {
                // When task completes, open mic autonomously so user can issue next request hands-free!
                startListening('🎙️ Listening... (Do you need anything more?)');
            });
        }
    } else if (state.status === 'error') {
        if (lastExecutionStatus !== 'error') {
            lastExecutionStatus = 'error';
            updateCoworkerStatus('Error Occurred', 'danger');
            const errorMsg = state.error || 'An error occurred during execution.';
            appendAssistantMessage(`⚠️ **Execution Notice:** ${errorMsg}`);
            speakCoworker(`Notice: ${errorMsg}`);
        }
    } else if (state.status === 'paused') {
        updateCoworkerStatus('Paused (Take Over)', 'warning');
    } else if (state.status === 'stopped') {
        updateCoworkerStatus('Stopped', 'danger');
    } else if (state.status === 'idle') {
        if (lastExecutionStatus === 'running') {
            lastExecutionStatus = 'idle';
        }
        updateCoworkerStatus('Coworker Ready', 'active');
    }

    // Check for interactive question modal from agent
    if (state.question && state.question_id && state.question_id !== lastQuestionId) {
        lastQuestionId = state.question_id;
        showHumanInTheLoopModal(state.question, state.question_id, state.question_audio_url);
    }

    // Update live browser workspace: stream in real-time when active, fallback to double-buffered screenshot when idle
    if (state.status === 'running' || state.status === 'paused') {
        activateLiveStream();
    } else {
        const latest = (state.screenshots && state.screenshots.length > 0) ? state.screenshots[state.screenshots.length - 1].path : null;
        if (isLiveStreamActive) {
            deactivateLiveStream(latest);
        } else if (latest) {
            updateRenderedScreenshot(latest);
        } else {
            updateRenderedScreenshot(null);
        }
    }

    // Render live timeline events (deduplicated in timeline container)
    if (state.events && state.events.length > 0) {
        renderTimeline(state.events);
    }
}

function renderTimeline(events) {
    const container = document.getElementById('inspectorTimeline');
    if (!container) return;

    container.innerHTML = '';
    events.slice(-15).forEach(ev => {
        const div = document.createElement('div');
        div.className = `timeline-entry ${ev.type || 'info'}`;
        const timeStr = ev.timestamp ? ev.timestamp.substring(11, 19) : '--:--:--';
        div.innerHTML = `<span class="time">${timeStr}</span> <span class="text">${escapeHtml(ev.message)}</span>`;
        container.appendChild(div);
    });
    container.scrollTop = container.scrollHeight;
}

async function pollState() {
    try {
        const res = await fetch('/api/state');
        if (res.ok) {
            const data = await res.json();
            handleStateUpdate(data);
        }
    } catch (e) {
        // ignore
    }
}

async function stopAgentExecution() {
    try {
        await fetch('/api/stop', { method: 'POST' });
        appendAssistantMessage('⏹️ Execution stopped by user.');
        updateCoworkerStatus('Stopped', 'warning');
    } catch (e) {
        console.error('Failed to stop:', e);
    }
}

async function pauseAgentExecution() {
    const pauseBtn = document.getElementById('pauseAgentBtn');
    const resumeBtn = document.getElementById('resumeAgentBtn');
    if (pauseBtn && resumeBtn) {
        pauseBtn.style.display = 'none';
        resumeBtn.style.display = 'inline-flex';
        resumeBtn.disabled = false;
    }
    updateCoworkerStatus('Paused (Take Over)', 'warning');
    toggleInteractiveMode(true);
    appendAssistantMessage('⏸️ **Agent Paused for Human Takeover**\n\nThe browser viewport is now under your direct control. Click anywhere on the browser workspace or type text into the input bar above. Click **Resume Auto** when you want AURA to resume autonomous execution.');
    try {
        await fetch('/api/pause', { method: 'POST' });
    } catch (e) {
        console.error('Failed to pause:', e);
    }
}

async function resumeAgentExecution() {
    const pauseBtn = document.getElementById('pauseAgentBtn');
    const resumeBtn = document.getElementById('resumeAgentBtn');
    if (pauseBtn && resumeBtn) {
        resumeBtn.style.display = 'none';
        pauseBtn.style.display = 'inline-flex';
        pauseBtn.disabled = false;
    }
    updateCoworkerStatus('Resuming...', 'active');
    appendAssistantMessage('▶️ **Resumed Autonomous Execution**\n\nAURA is inspecting your current page state and continuing towards the goal.');
    try {
        await fetch('/api/resume', { method: 'POST' });
    } catch (e) {
        console.error('Failed to resume:', e);
    }
}

async function clearConversation() {
    try {
        await fetch('/api/chat/clear', { method: 'POST' });
        const stream = document.getElementById('chatStream');
        stream.innerHTML = `
            <div class="chat-message assistant-message animate-fade" style="max-width: 100%; margin-top: 4vh; margin-bottom: 24px;">
                <div class="message-bubble" style="background: transparent; border: none; box-shadow: none; padding: 0; backdrop-filter: none; -webkit-backdrop-filter: none;">
                    <h1 style="font-size: clamp(2rem, 3vw, 3rem); font-weight: 800; line-height: 1.1; letter-spacing: -0.03em; max-width: 100%; text-wrap: balance; margin-bottom: 16px;">
                        Chat cleared. <br><span class="accent-text" style="font-size: clamp(1.5rem, 2.5vw, 2.5rem);">Ready for your next mission.</span>
                    </h1>
                </div>
            </div>
        `;
        updateCoworkerStatus('Coworker Ready', 'active');
    } catch (e) {
        console.error('Clear failed:', e);
    }
}

async function fetchBackendLogs() {
    const terminal = document.getElementById('backendTerminal');
    if (!terminal) return;
    try {
        const res = await fetch('/api/logs?limit=80');
        if (res.ok) {
            const data = await res.json();
            const logs = data.logs || [];
            if (logs.length === 0) {
                terminal.innerHTML = '<div style="color: #64748b;">[No recent logs recorded]</div>';
                return;
            }
            // Check if user has scrolled up to preserve position
            const isScrolledToBottom = terminal.scrollHeight - terminal.clientHeight <= terminal.scrollTop + 20;

            terminal.innerHTML = logs.map(entry => {
                let color = '#94a3b8';
                if (entry.level === 'WARNING') color = '#f59e0b';
                else if (entry.level === 'ERROR') color = '#ef4444';
                else if (entry.level === 'INFO') color = '#cbd5e1';

                return `<div style="color: ${color}; margin-bottom: 2px;"><span style="color: #475569;">[${entry.timestamp || ''}]</span> <strong style="color: #818cf8;">${entry.logger || ''}</strong>: ${escapeHtml(entry.message || '')}</div>`;
            }).join('');

            if (isScrolledToBottom) {
                terminal.scrollTop = terminal.scrollHeight;
            }
        }
    } catch (e) {
        // quiet fail on log fetch
    }
}

async function clearBackendLogs() {
    try {
        await fetch('/api/logs/clear', { method: 'POST' });
        const terminal = document.getElementById('backendTerminal');
        if (terminal) terminal.innerHTML = '<div style="color: #64748b;">[Logs cleared]</div>';
    } catch (e) {
        console.error('Failed to clear logs:', e);
    }
}

// =============================================================================
// Human-in-the-Loop Intervention Modal
// =============================================================================

function showHumanInTheLoopModal(questionText, questionId, audioUrl = null) {
    const modal = document.getElementById('questionModal');
    const title = document.getElementById('questionModalTitle');
    const inputsDiv = document.getElementById('questionModalInputs');

    if (title) title.innerText = questionText;
    if (inputsDiv) {
        inputsDiv.innerHTML = `
            <div style="position: relative; width: 100%;">
                <input type="text" id="modalAnswerInput" placeholder="Speak or type your answer..." style="width: 100%; padding-right: 42px;" autofocus />
                <button type="button" id="modalMicBtn" onclick="triggerModalSpeechListen()" style="position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; font-size: 16px; cursor: pointer;" title="Speak answer">🎙️</button>
            </div>
            <div id="modalVoiceNotice" style="font-size: 12px; color: #a5b4fc; margin-top: 8px; min-height: 18px;">
                🔊 Coworker is asking...
            </div>
        `;
        const input = document.getElementById('modalAnswerInput');
        if (input) {
            input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    submitModalAnswer();
                }
            });
        }
    }
    if (modal) modal.classList.remove('hidden');

    // 1. Coworker speaks question aloud
    updateCoworkerStatus('Asking for Guidance...', 'warning');
    speakCoworker(questionText, audioUrl, () => {
        // 2. Once coworker completes speaking, automatically open microphone!
        const notice = document.getElementById('modalVoiceNotice');
        if (notice) notice.innerHTML = '🎙️ <strong>Listening to your answer...</strong> (Speak clearly now)';
        startListeningForModal();
    });
}

function startListeningForModal() {
    if (!recognition) return;
    try {
        if (!isListening) {
            recognition.start();
        }
    } catch (e) {
        console.debug('Recognition start notice:', e);
    }
}

function triggerModalSpeechListen() {
    const notice = document.getElementById('modalVoiceNotice');
    if (notice) notice.innerHTML = '🎙️ <strong>Listening...</strong>';
    startListeningForModal();
}

async function submitModalAnswer() {
    stopListening(true);
    const modal = document.getElementById('questionModal');
    const input = document.getElementById('modalAnswerInput');
    const answer = input ? input.value.trim() : '';
    if (!answer) return;

    // Immediately hide modal and display answer in chat so UI never hangs
    if (modal) modal.classList.add('hidden');
    appendUserMessage(answer);
    if (!window.currentAppState || (window.currentAppState.status !== 'done' && window.currentAppState.status !== 'stopped')) {
        updateCoworkerStatus('Executing Task...', 'active');
    }

    try {
        await fetch('/api/answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer: answer })
        });
    } catch (e) {
        console.error('Failed to submit answer:', e);
    }
}

// =============================================================================
// Interactive Browser Workspace (Direct Click & Type Remote Control)
// =============================================================================

let isInteractiveMode = true;

function toggleInteractiveMode(forceState = null) {
    if (forceState !== null) {
        isInteractiveMode = !!forceState;
    } else {
        isInteractiveMode = !isInteractiveMode;
    }
    const dot = document.getElementById('interactStatusDot');
    const text = document.getElementById('interactStatusText');
    const box = document.getElementById('screenshotBox');
    
    if (dot) {
        dot.style.background = isInteractiveMode ? '#22c55e' : '#94a3b8';
        dot.style.boxShadow = isInteractiveMode ? '0 0 8px rgba(34, 197, 94, 0.6)' : 'none';
    }
    if (text) {
        text.innerText = isInteractiveMode ? 'Click-to-Interact: ON' : 'Click-to-Interact: OFF';
    }
    if (box) {
        box.style.cursor = isInteractiveMode ? 'crosshair' : 'pointer';
    }
}

async function handleViewportClick(event, element) {
    if (!isInteractiveMode) {
        openViewportModal();
        return;
    }

    // Identify target image element
    let img = document.getElementById('liveScreenshot');
    if (element && element.id === 'lightboxScreenshot') {
        img = element;
    }

    if (!img || img.style.display === 'none' || !img.src) {
        return;
    }

    const rect = img.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;

    const clientX = event.clientX;
    const clientY = event.clientY;

    // Check if clicked inside image bounding box
    if (clientX < rect.left || clientX > rect.right || clientY < rect.top || clientY > rect.bottom) {
        return;
    }

    const x_ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
    const y_ratio = Math.max(0, Math.min(1, (clientY - rect.top) / rect.height));

    // Show tactile ripple effect at click coordinates
    const container = img.parentElement || document.getElementById('screenshotBox');
    if (container) {
        const ripple = document.createElement('div');
        ripple.className = 'browser-click-ripple';
        ripple.style.left = `${clientX - rect.left}px`;
        ripple.style.top = `${clientY - rect.top}px`;
        container.appendChild(ripple);
        setTimeout(() => {
            if (ripple.parentNode) ripple.parentNode.removeChild(ripple);
        }, 650);
    }

    try {
        const res = await fetch('/api/browser/click', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ x_ratio, y_ratio })
        });
        if (res.ok) {
            const data = await res.json();
            if (data.screenshot) {
                updateRenderedScreenshot(data.screenshot, true);
            }
        }
    } catch (err) {
        console.error('Failed to dispatch browser click:', err);
    }
}

async function sendBrowserType(pressEnter = false) {
    const input = document.getElementById('browserTypeInput');
    const text = input ? input.value : '';
    if (!text && !pressEnter) return;

    try {
        const res = await fetch('/api/browser/type', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: text, press_enter: pressEnter })
        });
        if (input) input.value = '';
        if (res.ok) {
            const data = await res.json();
            if (data.screenshot) {
                updateRenderedScreenshot(data.screenshot, true);
            }
        }
    } catch (err) {
        console.error('Failed to type into browser:', err);
    }
}

function openViewportModal() {
    const liveImg = document.getElementById('liveScreenshot');
    const lightboxModal = document.getElementById('viewportModal');
    const lightboxImg = document.getElementById('lightboxScreenshot');
    const placeholder = document.getElementById('lightboxPlaceholder');
    const downloadBtn = document.getElementById('downloadScreenshotBtn');
    const lightboxUrl = document.getElementById('lightboxUrl');

    if (!lightboxModal) return;

    const currentSrc = isLiveStreamActive ? '/api/browser/stream' : (currentRenderedScreenshotUrl || (liveImg ? liveImg.src : ''));
    if (currentSrc && !currentSrc.endsWith('/') && !currentSrc.includes('about:blank')) {
        if (lightboxImg) {
            lightboxImg.src = currentSrc;
            lightboxImg.style.display = 'block';
        }
        if (placeholder) placeholder.style.display = 'none';
        if (downloadBtn) downloadBtn.href = currentSrc;
        if (lightboxUrl) {
            const stepEl = document.getElementById('inspectorStep');
            lightboxUrl.innerText = stepEl ? stepEl.innerText : 'Live Browser Viewport';
        }
    } else {
        if (lightboxImg) lightboxImg.style.display = 'none';
        if (placeholder) placeholder.style.display = 'flex';
        if (lightboxUrl) lightboxUrl.innerText = 'Browser Session Active';
    }
    lightboxModal.classList.remove('hidden');
}

function closeViewportModal(event = null) {
    if (event && event.target && event.target.closest('.viewport-lightbox-card')) {
        return; // clicked inside card
    }
    const lightboxModal = document.getElementById('viewportModal');
    if (lightboxModal) lightboxModal.classList.add('hidden');
}

// Global keydown handler for Escape key
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        closeViewportModal();
        closeSettingsModal();
    }
});

// =============================================================================
// Settings Modal & Dashboard Configuration
// =============================================================================

const openSettingsBtn = document.getElementById('openSettingsBtn');
if (openSettingsBtn) {
    openSettingsBtn.addEventListener('click', openSettingsModal);
}

async function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    modal.classList.remove('hidden');
    loadSettingsStatus();
}

function closeSettingsModal() {
    const modal = document.getElementById('settingsModal');
    modal.classList.add('hidden');
}

function toggleImapFields() {
    const provider = document.getElementById('cfgEmailProvider').value;
    const imapDiv = document.getElementById('imapFields');
    if (provider === 'imap') {
        imapDiv.classList.remove('hidden');
    } else {
        imapDiv.classList.add('hidden');
    }
}

async function loadSettingsStatus() {
    try {
        const res = await fetch('/api/settings');
        if (!res.ok) return;
        const cfg = await res.json();

        // Status badges
        const bAnakin = document.getElementById('badgeAnakin');
        const bLlm = document.getElementById('badgeLlm');
        const bGroq = document.getElementById('badgeGroq');
        const bTts = document.getElementById('badgeTts');

        if (bAnakin) {
            bAnakin.innerText = cfg.anakin_configured ? 'Configured' : 'Missing';
            bAnakin.className = `badge-status ${cfg.anakin_configured ? 'active' : ''}`;
        }
        if (bLlm) {
            bLlm.innerText = cfg.nvidia_llm_configured ? 'Configured' : 'Missing';
            bLlm.className = `badge-status ${cfg.nvidia_llm_configured ? 'active' : ''}`;
        }
        if (bGroq) {
            bGroq.innerText = cfg.groq_configured ? 'Configured' : 'Missing';
            bGroq.className = `badge-status ${cfg.groq_configured ? 'active' : ''}`;
        }
        if (bTts) {
            bTts.innerText = cfg.nvidia_tts_configured ? 'Configured' : 'Missing';
            bTts.className = `badge-status ${cfg.nvidia_tts_configured ? 'active' : ''}`;
        }

        // Form fields
        const llmModel = document.getElementById('cfgLlmModel');
        if (llmModel && cfg.nvidia_llm_model) llmModel.value = cfg.nvidia_llm_model;

        const groqModel = document.getElementById('cfgGroqModel');
        if (groqModel && cfg.groq_model) groqModel.value = cfg.groq_model;

        const emailProv = document.getElementById('cfgEmailProvider');
        if (emailProv) emailProv.value = cfg.email_provider || 'local';
        toggleImapFields();

        const imapHost = document.getElementById('cfgImapHost');
        if (imapHost && cfg.imap_host) imapHost.value = cfg.imap_host;

        const imapPort = document.getElementById('cfgImapPort');
        if (imapPort && cfg.imap_port) imapPort.value = cfg.imap_port;

        const imapUser = document.getElementById('cfgImapUser');
        if (imapUser && cfg.imap_user) imapUser.value = cfg.imap_user;

        const headless = document.getElementById('cfgHeadless');
        if (headless) headless.checked = cfg.headless || false;

    } catch (e) {
        console.warn('Failed to load settings:', e);
    }
}

async function saveSettings(e) {
    e.preventDefault();
    const btn = document.getElementById('saveSettingsBtn');
    btn.disabled = true;
    btn.innerText = 'Saving...';

    const payload = {};
    const anakinKey = document.getElementById('cfgAnakinKey').value.trim();
    if (anakinKey) payload.anakin_api_key = anakinKey;

    const llmKey = document.getElementById('cfgLlmKey').value.trim();
    if (llmKey) payload.nvidia_llm_api_key = llmKey;

    const llmModel = document.getElementById('cfgLlmModel').value.trim();
    if (llmModel) payload.nvidia_llm_model = llmModel;

    const groqKey = document.getElementById('cfgGroqKey').value.trim();
    if (groqKey) payload.groq_api_key = groqKey;

    const groqModel = document.getElementById('cfgGroqModel').value.trim();
    if (groqModel) payload.groq_model = groqModel;

    const ttsKey = document.getElementById('cfgTtsKey').value.trim();
    if (ttsKey) payload.nvidia_tts_api_key = ttsKey;

    payload.email_provider = document.getElementById('cfgEmailProvider').value;
    if (payload.email_provider === 'imap') {
        payload.imap_host = document.getElementById('cfgImapHost').value.trim();
        payload.imap_port = parseInt(document.getElementById('cfgImapPort').value) || 993;
        payload.imap_user = document.getElementById('cfgImapUser').value.trim();
        const imapPass = document.getElementById('cfgImapPassword').value.trim();
        if (imapPass) payload.imap_password = imapPass;
    }

    payload.headless = document.getElementById('cfgHeadless').checked;

    try {
        const res = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            btn.innerText = 'Saved!';
            setTimeout(() => {
                closeSettingsModal();
                btn.disabled = false;
                btn.innerText = 'Save Settings';
            }, 800);
            loadSettingsStatus();
        } else {
            alert('Failed to save settings');
            btn.disabled = false;
            btn.innerText = 'Save Settings';
        }
    } catch (err) {
        alert('Settings update error: ' + err.message);
        btn.disabled = false;
        btn.innerText = 'Save Settings';
    }
}

// =============================================================================
// Helpers
// =============================================================================

function updateCoworkerStatus(text, type = 'active') {
    const badgeText = document.getElementById('coworkerStatusText');
    const badge = document.getElementById('coworkerStatusBadge');
    if (!badgeText || !badge) return;

    badgeText.innerText = text;
    badge.className = 'status-badge';
    if (type === 'warning') {
        badge.style.color = '#f59e0b';
        badge.style.borderColor = 'rgba(245, 158, 11, 0.4)';
    } else if (type === 'danger') {
        badge.style.color = '#ef4444';
        badge.style.borderColor = 'rgba(239, 68, 68, 0.4)';
    } else {
        badge.style.color = '#34d399';
        badge.style.borderColor = 'rgba(16, 185, 129, 0.25)';
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function escapeAttr(str) {
    if (!str) return '';
    return str.replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

function formatMessageText(text) {
    if (!text) return '';
    let escaped = escapeHtml(text);
    // Markdown headers: ### Title or ## Title
    escaped = escaped.replace(/^###\s+(.*?)$/gm, '<h3 style="margin: 10px 0 6px 0; color: var(--text-main); font-size: 1.05rem; font-weight: 700;">$1</h3>');
    escaped = escaped.replace(/^##\s+(.*?)$/gm, '<h2 style="margin: 12px 0 8px 0; color: var(--text-main); font-size: 1.15rem; font-weight: 700;">$1</h2>');
    // Markdown bold: **text**
    escaped = escaped.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Markdown italics: *text*
    escaped = escaped.replace(/\*(.*?)\*/g, '<em>$1</em>');
    // Markdown links: [Title](URL)
    escaped = escaped.replace(/\[(.*?)\]\((https?:\/\/[^\s\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer" style="color: var(--primary); text-decoration: underline; font-weight: 600;">$1 ↗</a>');
    // Line breaks
    escaped = escaped.replace(/\n/g, '<br>');
    return escaped;
}

// =============================================================================
// Modals & Controls Management
// =============================================================================

function openSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) {
        modal.classList.remove('hidden');
        loadSettingsStatus();
    }
}

function closeSettingsModal() {
    const modal = document.getElementById('settingsModal');
    if (modal) modal.classList.add('hidden');
}

// =============================================================================
// Theme Management
// =============================================================================

function toggleTheme() {
    document.body.classList.add('theme-transitioning');
    currentTheme = currentTheme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', currentTheme);
    localStorage.setItem('aura_theme', currentTheme);
    updateThemeUI();
    setTimeout(() => {
        document.body.classList.remove('theme-transitioning');
    }, 350);
}

function updateThemeUI() {
    const moonIcon = document.getElementById('moonIcon');
    const sunIcon = document.getElementById('sunIcon');
    if (moonIcon && sunIcon) {
        if (currentTheme === 'dark') {
            moonIcon.style.display = 'none';
            sunIcon.style.display = 'inline-block';
        } else {
            moonIcon.style.display = 'inline-block';
            sunIcon.style.display = 'none';
        }
    }
}

// =============================================================================
// Logs Toggle Management
// =============================================================================

function toggleLogsView() {
    const container = document.getElementById('logsContainer');
    const icon = document.getElementById('toggleLogsIcon');
    const btn = document.getElementById('toggleLogsBtn');

    if (container.style.display === 'none') {
        container.style.display = 'flex';
        icon.innerText = '▲';
        btn.innerHTML = '<span id="toggleLogsIcon">▲</span> Hide Technical Logs';
    } else {
        container.style.display = 'none';
        icon.innerText = '▼';
        btn.innerHTML = '<span id="toggleLogsIcon">▼</span> Show Technical Logs';
    }
}
