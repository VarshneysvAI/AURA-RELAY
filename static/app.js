/**
 * AURA Relay Frontend Application
 */

// State
let currentQuestionId = null;
let eventSource = null;
let recognition = null;
let isListening = false;

// DOM Elements
const elements = {
    instruction: document.getElementById('instruction'),
    startBtn: document.getElementById('startBtn'),
    stopBtn: document.getElementById('stopBtn'),
    retryBtn: document.getElementById('retryBtn'),
    statusValue: document.getElementById('statusValue'),
    currentStep: document.getElementById('currentStep'),
    nextStep: document.getElementById('nextStep'),
    reasonValue: document.getElementById('reasonValue'),
    timeline: document.getElementById('timeline'),
    mainScreenshot: document.getElementById('mainScreenshot'),
    noScreenshot: document.getElementById('noScreenshot'),
    screenshotThumbnails: document.getElementById('screenshotThumbnails'),
    emailList: document.getElementById('emailList'),
    questionContainer: document.getElementById('questionContainer'),
    questionText: document.getElementById('questionText'),
    answerInput: document.getElementById('answerInput'),
    sendAnswerBtn: document.getElementById('sendAnswerBtn'),
    voiceBtn: document.getElementById('voiceBtn'),
    voiceStatus: document.getElementById('voiceStatus'),
    resultSection: document.getElementById('resultSection'),
    resultStatus: document.getElementById('resultStatus'),
    resultMessage: document.getElementById('resultMessage'),
    resultEvidence: document.getElementById('resultEvidence'),
    envBadge: document.getElementById('envBadge')
};

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initVoiceRecognition();
    loadInitialState();
    setupEventListeners();
    connectToEvents();
    checkEnvironment();
});

// Voice Recognition Setup
function initVoiceRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    
    if (SpeechRecognition) {
        recognition = new SpeechRecognition();
        recognition.continuous = false;
        recognition.interimResults = false;
        recognition.lang = 'en-US';
        
        recognition.onresult = (event) => {
            const transcript = event.results[0][0].transcript;
            elements.answerInput.value = transcript;
            stopListening();
        };
        
        recognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            stopListening();
        };
        
        recognition.onend = () => {
            stopListening();
        };
        
        elements.voiceStatus.textContent = 'Voice: Ready (click mic to speak)';
        elements.voiceBtn.disabled = false;
    } else {
        elements.voiceStatus.textContent = 'Voice: Not supported in this browser';
        elements.voiceBtn.disabled = true;
    }
}

function startListening() {
    if (recognition && !isListening) {
        try {
            recognition.start();
            isListening = true;
            elements.voiceBtn.style.background = '#ef4444';
        } catch (e) {
            console.error('Failed to start recognition:', e);
        }
    }
}

function stopListening() {
    if (recognition && isListening) {
        recognition.stop();
        isListening = false;
        elements.voiceBtn.style.background = '';
    }
}

// Event Listeners
function setupEventListeners() {
    elements.startBtn.addEventListener('click', startAgent);
    elements.stopBtn.addEventListener('click', stopAgent);
    elements.retryBtn.addEventListener('click', retryAgent);
    elements.sendAnswerBtn.addEventListener('click', sendAnswer);
    elements.voiceBtn.addEventListener('click', toggleVoice);
    
    // Poll for state updates as backup
    setInterval(pollState, 2000);
    setInterval(pollInbox, 3000);
    setInterval(pollScreenshots, 5000);
}

function toggleVoice() {
    if (isListening) {
        stopListening();
    } else {
        startListening();
    }
}

// API Calls
async function loadInitialState() {
    try {
        const response = await fetch('/api/state');
        const state = await response.json();
        updateUI(state);
    } catch (e) {
        console.error('Failed to load initial state:', e);
    }
}

async function startAgent() {
    const instruction = elements.instruction.value.trim();
    if (!instruction) {
        alert('Please enter an instruction');
        return;
    }
    
    try {
        const response = await fetch('/api/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ instruction })
        });
        
        if (response.ok) {
            addTimelineItem('info', 'Agent started', instruction);
            updateButtons(true);
        } else {
            const error = await response.json();
            alert(error.detail || 'Failed to start agent');
        }
    } catch (e) {
        console.error('Failed to start agent:', e);
        alert('Failed to start agent');
    }
}

async function stopAgent() {
    try {
        const response = await fetch('/api/stop', { method: 'POST' });
        if (response.ok) {
            addTimelineItem('warning', 'Agent stopped by user');
            updateButtons(false);
        }
    } catch (e) {
        console.error('Failed to stop agent:', e);
    }
}

async function retryAgent() {
    try {
        const response = await fetch('/api/retry', { method: 'POST' });
        if (response.ok) {
            addTimelineItem('info', 'Retrying last step');
            updateButtons(true);
        }
    } catch (e) {
        console.error('Failed to retry:', e);
    }
}

async function sendAnswer() {
    const answer = elements.answerInput.value.trim();
    if (!answer || !currentQuestionId) return;
    
    try {
        const response = await fetch('/api/answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer })
        });
        
        if (response.ok) {
            addTimelineItem('success', 'Answer sent', answer);
            elements.answerInput.value = '';
            elements.answerInput.disabled = true;
            elements.sendAnswerBtn.disabled = true;
            currentQuestionId = null;
            
            // Speak confirmation
            speak('Answer submitted. Continuing...');
        } else {
            alert('Failed to send answer');
        }
    } catch (e) {
        console.error('Failed to send answer:', e);
    }
}

// Server-Sent Events
function connectToEvents() {
    eventSource = new EventSource('/api/events');
    
    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleEvent(data);
    };
    
    eventSource.onerror = () => {
        console.log('Event source connection lost, reconnecting...');
        setTimeout(connectToEvents, 3000);
    };
}

function handleEvent(data) {
    const { type, message, timestamp, state } = data;
    
    // Add to timeline
    addTimelineItem(getEventType(type), message, timestamp);
    
    // Update state if included
    if (state) {
        updateUIFromState(state);
    }
    
    // Handle questions
    if (type === 'user_question' && state?.question) {
        handleQuestion(state.question, state.question_id);
    }
}

function getEventType(serverType) {
    const mapping = {
        'plan_created': 'info',
        'step_started': 'info',
        'page_opened': 'info',
        'element_found': 'success',
        'element_not_found': 'warning',
        'retrying': 'warning',
        'action_started': 'info',
        'action_succeeded': 'success',
        'action_failed': 'error',
        'otp_required': 'warning',
        'email_received': 'success',
        'otp_found': 'success',
        'otp_filled': 'success',
        'user_question': 'question',
        'user_answer_received': 'info',
        'completed': 'success',
        'error': 'error'
    };
    return mapping[serverType] || 'info';
}

// UI Updates
function updateUI(state) {
    updateUIFromState(state);
    updateButtons(state.status === 'running' || state.status === 'paused');
    
    if (state.screenshots && state.screenshots.length > 0) {
        updateScreenshots(state.screenshots);
    }
    
    if (state.result) {
        showResult(state);
    }
}

function updateUIFromState(state) {
    // Status
    elements.statusValue.textContent = state.status;
    elements.statusValue.className = `status-value status-${state.status}`;
    
    // Steps
    elements.currentStep.textContent = state.current_step || '-';
    elements.nextStep.textContent = state.next_step || '-';
    elements.reasonValue.textContent = state.reason || 'Waiting...';
    
    // Question
    if (state.question) {
        handleQuestion(state.question, state.question_id);
    }
}

function updateButtons(isRunning) {
    elements.startBtn.disabled = isRunning;
    elements.stopBtn.disabled = !isRunning;
    elements.retryBtn.disabled = isRunning;
    elements.instruction.disabled = isRunning;
}

function addTimelineItem(type, message, timeStr) {
    const now = new Date();
    const time = timeStr ? new Date(timeStr).toLocaleTimeString() : now.toLocaleTimeString();
    
    const item = document.createElement('div');
    item.className = `timeline-item ${type}`;
    item.innerHTML = `
        <div class="timeline-time">${time}</div>
        <div class="timeline-content">
            <div class="timeline-type">${type}</div>
            <div class="timeline-message">${escapeHtml(message)}</div>
        </div>
    `;
    
    elements.timeline.insertBefore(item, elements.timeline.firstChild);
    
    // Limit items
    while (elements.timeline.children.length > 50) {
        elements.timeline.removeChild(elements.timeline.lastChild);
    }
}

function handleQuestion(question, questionId) {
    currentQuestionId = questionId;
    elements.questionText.textContent = question;
    elements.answerInput.disabled = false;
    elements.sendAnswerBtn.disabled = false;
    elements.answerInput.focus();
    
    // Speak the question
    speak(question);
}

function speak(text) {
    if ('speechSynthesis' in window) {
        speechSynthesis.cancel(); // Stop any previous speech
        
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1;
        utterance.pitch = 1;
        
        utterance.onend = () => {
            // AUTO-LISTEN: Automatically start listening after speaking
            if (recognition && !isListening) {
                setTimeout(() => {
                    try {
                        recognition.start();
                        isListening = true;
                        if (elements.voiceBtn) elements.voiceBtn.style.background = '#ef4444';
                        if (elements.voiceStatus) elements.voiceStatus.textContent = 'Voice: Listening...';
                    } catch(e) {
                        console.error('Auto-listen failed:', e);
                    }
                }, 500); // 500ms delay for mic to be ready
            }
        };
        
        speechSynthesis.speak(utterance);
    }
}

function speakOLD(text) {
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 1;
        utterance.pitch = 1;
        speechSynthesis.speak(utterance);
    }
}

function updateScreenshots(screenshots) {
    if (screenshots.length === 0) return;
    
    const latest = screenshots[screenshots.length - 1];
    elements.mainScreenshot.src = latest.path;
    elements.mainScreenshot.classList.add('active');
    elements.noScreenshot.style.display = 'none';
    
    // Thumbnails
    elements.screenshotThumbnails.innerHTML = '';
    screenshots.forEach((shot, index) => {
        const img = document.createElement('img');
        img.src = shot.path;
        img.className = 'thumbnail' + (index === screenshots.length - 1 ? ' active' : '');
        img.onclick = () => {
            elements.mainScreenshot.src = shot.path;
            document.querySelectorAll('.thumbnail').forEach(t => t.classList.remove('active'));
            img.classList.add('active');
        };
        elements.screenshotThumbnails.appendChild(img);
    });
}

async function pollState() {
    try {
        const response = await fetch('/api/state');
        const state = await response.json();
        updateUIFromState(state);
        
        if (state.status === 'done' || state.status === 'error' || state.status === 'stopped') {
            updateButtons(false);
            if (state.result) {
                showResult(state);
            }
        }
    } catch (e) {
        // Ignore polling errors
    }
}

async function pollInbox() {
    try {
        const response = await fetch('/api/inbox');
        const data = await response.json();
        updateEmailList(data.emails);
    } catch (e) {
        // Ignore
    }
}

async function pollScreenshots() {
    try {
        const response = await fetch('/api/screenshots');
        const screenshots = await response.json();
        if (screenshots.length > 0) {
            updateScreenshots(screenshots);
        }
    } catch (e) {
        // Ignore
    }
}

function updateEmailList(emails) {
    if (emails.length === 0) {
        elements.emailList.innerHTML = '<div class="email-empty">Waiting for emails...</div>';
        return;
    }
    
    elements.emailList.innerHTML = '';
    emails.forEach(email => {
        const item = document.createElement('div');
        item.className = 'email-item';
        
        let otpDisplay = '';
        if (email.otp_code) {
            const masked = '*'.repeat(email.otp_code.length - 2) + email.otp_code.slice(-2);
            otpDisplay = `<div class="email-otp">OTP: ${masked}</div>`;
        }
        
        item.innerHTML = `
            <div class="email-sender">${escapeHtml(email.sender)}</div>
            <div class="email-subject">${escapeHtml(email.subject)}</div>
            <div class="email-body">${escapeHtml(email.body.substring(0, 100))}...</div>
            ${otpDisplay}
        `;
        elements.emailList.appendChild(item);
    });
}

function showResult(state) {
    elements.resultSection.style.display = 'block';
    elements.resultStatus.textContent = state.status.toUpperCase();
    elements.resultStatus.className = `result-status ${state.status}`;
    elements.resultMessage.textContent = state.result || state.error_message || '';
    
    // Evidence
    if (state.screenshots && state.screenshots.length > 0) {
        const latest = state.screenshots[state.screenshots.length - 1];
        elements.resultEvidence.innerHTML = `
            <img src="${latest.path}" style="max-height: 200px; border-radius: 8px;" />
        `;
    }
}

function checkEnvironment() {
    fetch('/health')
        .then(r => r.json())
        .then(data => {
            elements.envBadge.textContent = data.mode === 'sandbox' ? 'Sandbox' : 'Live';
            elements.envBadge.className = `environment-badge ${data.mode}`;
        })
        .catch(() => {
            elements.envBadge.textContent = 'Unknown';
        });
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
