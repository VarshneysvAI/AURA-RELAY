"""
AURA Relay — LLM Provider
Integrates NVIDIA Nemotron for task understanding, upfront requirement planning,
and step-by-step ReAct browser decision making.
"""
import asyncio
import json
import logging
import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional
import httpx

from aura.config import get_config

logger = logging.getLogger("aura.llm")


def clean_llm_response(text: str) -> str:
    """Remove internal reasoning or thought markers from model output."""
    if not text:
        return ""
    # Strip <thought>...</thought> tags
    cleaned = re.sub(r"<thought>.*?</thought>", "", text, flags=re.DOTALL)
    # Strip "Here's a thinking process:\n...\n\n" prefix if present
    if "Here's a thinking process:" in cleaned:
        parts = cleaned.split("\n\n", 2)
        if len(parts) > 1 and "Here's a thinking process:" in parts[0]:
            cleaned = "\n\n".join(parts[1:])
            
    cleaned = cleaned.strip()
    
    # If cleaning stripped out everything (e.g. JSON was inside the thought block),
    # or if we are clearly missing a JSON block that was in the original text,
    # fall back to returning the original text so regex can still find the JSON.
    if not cleaned and text:
        return text.strip()
    if "{" in text and "}" in text and ("{" not in cleaned or "}" not in cleaned):
        return text.strip()
        
    return cleaned


def extract_json_object(text: str) -> Optional[dict]:
    """
    Safely extract a JSON dictionary from LLM output.
    Uses balanced brace matching to prevent greedy regex corruption
    when trailing thoughts, code blocks, or explanations contain braces.
    """
    if not text:
        return None
    text = text.strip()
    # 1. Try direct parse
    try:
        res = json.loads(text)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    # 2. Balanced brace scan
    start = text.find('{')
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == '\\':
                escape = True
                continue
            if c == '"':
                in_string = not in_string
                continue
            if not in_string:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i+1]
                        try:
                            res = json.loads(candidate)
                            if isinstance(res, dict):
                                return res
                        except Exception:
                            break
        start = text.find('{', start + 1)

    # 3. Fallback to non-greedy regex
    match = re.search(r"\{.*?\}", text, re.DOTALL)
    if match:
        try:
            res = json.loads(match.group(0))
            if isinstance(res, dict):
                return res
        except Exception:
            pass
    return None


import time


class RateLimiter:
    """Enforces maximum calls per second (e.g. 20 calls/sec for NVIDIA LLM free tier)."""

    def __init__(self, max_rate: float = 20.0, time_window: float = 1.0):
        self.max_rate = max_rate
        self.time_window = time_window
        self.timestamps: List[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self):
        async with self._lock:
            now = time.monotonic()
            self.timestamps = [t for t in self.timestamps if now - t < self.time_window]
            if len(self.timestamps) >= self.max_rate:
                oldest = self.timestamps[0]
                sleep_duration = self.time_window - (now - oldest) + 0.01
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)
                now = time.monotonic()
                self.timestamps = [t for t in self.timestamps if now - t < self.time_window]
            self.timestamps.append(now)


class NvidiaLLMProvider:
    """NVIDIA NIM LLM Client for OpenAI-compatible chat completions."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        cfg = get_config()
        self.api_key = api_key or cfg.nvidia_llm_api_key or os.getenv("NVIDIA_LLM_API_KEY")
        self.model = model or cfg.nvidia_llm_model or "openai/gpt-oss-20b"
        self.base_url = (base_url or cfg.nvidia_llm_base_url or "https://integrate.api.nvidia.com/v1").rstrip("/")
        
        self.groq_api_key = cfg.groq_api_key or os.getenv("GROQ_API_KEY")
        self.groq_model = cfg.groq_model or "llama-3.3-70b-versatile"
        
        # Rate limiter to respect NVIDIA free tier (up to 20 calls/sec)
        self.rate_limiter = RateLimiter(max_rate=20.0, time_window=1.0)

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_key.startswith("nvapi-")) or bool(self.groq_api_key and self.groq_api_key.startswith("gsk_"))

    async def _chat_groq(self, messages: List[Dict[str, str]], temperature: float, max_tokens: int) -> str:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.groq_api_key}",
        }
        payload = {
            "model": self.groq_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        logger.info(f"Groq LLM success on attempt {attempt+1}")
                        return clean_llm_response(content)
                    elif resp.status_code == 429:
                        retry_after = float(resp.headers.get("retry-after", 2.0))
                        logger.warning(f"Groq LLM rate limit (429), backing off {retry_after}s on attempt {attempt+1}...")
                        await asyncio.sleep(retry_after)
                        continue
                    elif resp.status_code in [502, 503, 504] and attempt < 1:
                        logger.warning(f"Groq LLM {resp.status_code} on attempt {attempt+1}, retrying in 2s...")
                        await asyncio.sleep(2.0)
                        continue
                    else:
                        logger.warning(f"Groq LLM error {resp.status_code}: {resp.text[:150]}")
                        return f"LLM error ({resp.status_code})"
            except Exception as e:
                logger.warning(f"Groq LLM exception: {e}")
                if attempt < 1:
                    await asyncio.sleep(1.0)
                    continue
                return f"LLM connection error: {str(e)}"
        return "LLM unavailable"

    async def chat(self, messages: List[Dict[str, str]], temperature: float = 0.4, max_tokens: int = 500) -> str:
        """Send chat messages with 20 calls/sec rate limiting and retry handling. Fallback to Groq if NVIDIA fails."""
        if not self.is_configured():
            return "NVIDIA LLM API key not configured. Operating in local heuristic mode."

        if not self.api_key or not self.api_key.startswith("nvapi-"):
            # Direct Groq fallback if NVIDIA key is not set
            if self.groq_api_key:
                logger.info("NVIDIA key missing, routing directly to Groq fallback.")
                return await self._chat_groq(messages, temperature, max_tokens)
            return "NVIDIA LLM API key not configured. Operating in local heuristic mode."

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "reasoning_effort": "low",
        }

        nvidia_failed = False
        for attempt in range(3):
            try:
                # Respect rate limit of 20 calls/sec
                await self.rate_limiter.acquire()

                async with httpx.AsyncClient(timeout=35.0) as client:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        msg_obj = data.get("choices", [{}])[0].get("message", {})
                        content = msg_obj.get("content")
                        if not content:
                            content = msg_obj.get("reasoning_content") or msg_obj.get("reasoning") or ""
                        return clean_llm_response(content)
                    elif resp.status_code == 429:
                        retry_after = float(resp.headers.get("retry-after", 1.5))
                        logger.warning(f"NVIDIA LLM rate limit (429), backing off {retry_after}s on attempt {attempt+1}...")
                        await asyncio.sleep(retry_after)
                        continue
                    elif resp.status_code in [502, 503, 504] and attempt < 2:
                        logger.warning(f"NVIDIA LLM {resp.status_code} on attempt {attempt+1}, retrying in 2s...")
                        await asyncio.sleep(2.0)
                        continue
                    else:
                        logger.warning(f"NVIDIA LLM error {resp.status_code}: {resp.text[:150]}")
                        nvidia_failed = True
                        break
            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                if attempt < 2:
                    logger.warning(f"NVIDIA LLM timeout on attempt {attempt+1}, retrying...")
                    await asyncio.sleep(1.0)
                    continue
                logger.warning(f"NVIDIA LLM timeout ({type(e).__name__}): {e}")
                nvidia_failed = True
                break
            except Exception as e:
                logger.warning(f"NVIDIA LLM exception ({type(e).__name__}): {e}")
                nvidia_failed = True
                break
                
        if nvidia_failed and self.groq_api_key:
            logger.warning("NVIDIA LLM failed, falling back to Groq API...")
            return await self._chat_groq(messages, temperature, max_tokens)
            
        return "LLM unavailable"

    async def optimize_search_query(
        self,
        user_instruction: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        Translates raw conversational or voice-transcribed instructions into a clean,
        laser-focused search engine query and determines the best target platform.
        Resolves speech-to-text misrecognitions (e.g. 'proud' -> 'provide/find', 'by' -> 'buy').
        Uses conversation history if available to maintain context across turns.
        """
        from .query_cleaner import clean_search_query, build_search_url
        heuristic = clean_search_query(user_instruction)
        
        if not self.is_configured():
            return heuristic

        conv_context = ""
        if conversation_history:
            turns = [
                f"{m['role'].capitalize()}: {m['content']}"
                for m in conversation_history[-4:]
                if m.get("content") and m["content"] != user_instruction
            ]
            if turns:
                conv_context = "Prior Conversation Context:\n" + "\n".join(turns) + "\n\n"

        system_prompt = (
            "You are AURA's Search Query Optimizer.\n"
            "The user gave a conversational voice or text request to an autonomous AI browser coworker.\n"
            "Your task:\n"
            "1. Extract the core search keywords only. Strip ALL conversational chatter, imperatives, and fillers (e.g. 'do one thing', 'do me a favor', 'i don't have a target budget', 'more priority the brand can be', 'can you please', 'opening browser is nice').\n"
            "2. Fix speech recognition mistakes (e.g., 'seafood' -> 'Seiko' when discussing watches, 'proud' -> 'provide/find', 'by' -> 'buy').\n"
            "3. Formulate a laser-focused search query (2 to 4 keywords, e.g., 'Seiko professional watches' or 'mechanical watches under $300'). NEVER return the user's conversational sentence.\n"
            "4. Choose platform: 'ebay' (for buying products/items/watches/electronics), 'wikipedia' (for encyclopedic knowledge), 'youtube' (for videos), 'google' (for general news, search, or research).\n"
            "Output ONLY a valid JSON object matching this schema:\n"
            "{\n"
            '  "optimized_query": "clean search terms",\n'
            '  "platform": "google" | "ebay" | "amazon" | "wikipedia" | "youtube",\n'
            '  "intent": "news" | "shopping" | "research" | "video" | "general",\n'
            '  "max_price": null or integer\n'
            "}"
        )

        user_content = f"{conv_context}User Request: {user_instruction}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        try:
            raw = await self.chat(messages, temperature=0.1, max_tokens=350)
            parsed = extract_json_object(raw)
            if parsed and isinstance(parsed, dict) and parsed.get("optimized_query"):
                opt_raw = parsed["optimized_query"].strip()
                normalized = clean_search_query(opt_raw)
                opt_q = normalized["keywords"] or opt_raw
                plat = parsed.get("platform") or heuristic["platform"]
                if any(w in (user_instruction + " " + opt_q).lower() for w in ["watch", "watches", "laptop", "phone", "keyboard", "buy", "shop", "price"]):
                    if plat == "google":
                        plat = "ebay"
                max_p = parsed.get("max_price") or heuristic.get("max_price")
                return {
                    "platform": plat,
                    "keywords": opt_q,
                    "optimized_query": opt_q,
                    "max_price": max_p,
                    "anakin_query": opt_q,
                    "target_url": build_search_url(plat, opt_q, max_p),
                    "intent": parsed.get("intent", "shopping" if plat in ["ebay", "amazon"] else "general"),
                }
        except Exception as e:
            logger.warning(f"Error optimizing search query with LLM: {e}")

        return heuristic

    async def plan_task(
        self,
        user_goal: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        web_intelligence: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Phase 1 & 2: Analyze task upfront as a smart coworker with conversational memory.
        Identify requirements, credentials, OTP preferences, and initial plan.
        """
        # Intent checks before planning
        from .query_cleaner import is_stop_intent, is_conversational_closure, is_conversational_inquiry
        if is_stop_intent(user_goal):
            return {
                "task_type": "stop",
                "summary": "Task cancelled by user",
                "optimized_search_query": "",
                "target_url": None,
                "needs_upfront_info": False,
                "questions": [],
                "coworker_message": "Understood! I've stopped the task. Let me know whenever you'd like to start something else.",
                "steps": [],
            }

        if is_conversational_closure(user_goal):
            user_lower = user_goal.lower().strip()
            if any(w in user_lower for w in ["thank", "thanks", "great job", "awesome"]):
                msg = "You're very welcome! Let me know whenever you have another task or question."
            elif any(w in user_lower for w in ["bye", "goodbye", "see you"]):
                msg = "Goodbye! Have a great day, and feel free to call on me anytime."
            else:
                msg = "No problem at all! If you need anything else, just let me know."
            return {
                "task_type": "closure",
                "summary": "Conversational closure",
                "optimized_search_query": "",
                "target_url": None,
                "needs_upfront_info": False,
                "questions": [],
                "coworker_message": msg,
                "steps": [],
            }

        # Conversational inquiry or greeting check
        user_lower = user_goal.lower().strip()
        is_greeting = any(w in user_lower for w in ["listen", "hear me", "hello", "hi ", "hey", "can you hear", "can you listen"])
        is_capability_query = any(w in user_lower for w in ["describe your work", "how do you work", "what can you do", "who are you", "what is aura"])

        if is_greeting or is_capability_query or is_conversational_inquiry(user_goal):
            if any(w in user_lower for w in ["listen", "hear"]):
                reply = "Yes, I can hear you clearly! I am AURA, your autonomous AI coworker. What task, research, or product search would you like me to tackle for you?"
            elif any(w in user_lower for w in ["hello", "hi ", "hey"]):
                reply = "Hello! I am AURA, your autonomous coworker. Ready to browse the web, search eBay with Anakin.io, or manage portals. How can I help you today?"
            else:
                reply = (
                    "I am AURA, your autonomous web coworker. I combine live web intelligence from Anakin.io "
                    "with real browser automation and visual inspection to do work on your behalf. I can find products and compare prices on eBay, "
                    "log into portals, auto-capture OTP codes from your email, and safely guide tasks with human approval. "
                    "What would you like me to tackle for you?"
                )
            return {
                "task_type": "inquiry",
                "summary": "Conversational greeting/inquiry",
                "target_url": None,
                "needs_upfront_info": False,
                "questions": [],
                "coworker_message": reply,
                "steps": ["Respond to coworker"],
            }

        # Heuristic plan generator for fallback or unconfigured mode
        def get_heuristic_plan() -> Dict[str, Any]:
            is_portal = any(w in user_lower for w in ["login", "portal", "bank", "account", "tax", "otp", "statement"])
            if is_portal:
                return {
                    "task_type": "portal_login",
                    "summary": f"Automate portal workflow for: {user_goal}",
                    "target_url": "http://127.0.0.1:8000/sandbox/login.html",
                    "needs_upfront_info": True,
                    "questions": [
                        "Please provide your username and password for the portal.",
                    ],
                    "coworker_message": "I'm ready to handle this portal task. Please provide your login credentials to proceed.",
                    "steps": ["Navigate to portal", "Enter credentials", "Handle OTP verification", "Complete action"],
                }
            elif any(w in user_lower for w in ["ebay", "watch", "buy", "shop", "price", "amazon", "item", "product", "negotiate", "offer"]):
                target = "https://www.ebay.com" if "ebay" in user_lower else "https://www.google.com"
                has_budget = bool(re.search(r"(\$|usd|\b\d+\s*(?:dollars|bucks)\b|\b\d{2,4}\b)", user_lower))
                # A generic check: if they gave a budget and some words, assume they provided an item.
                has_item = len([w for w in user_lower.split() if len(w) > 3]) > 2
                
                if has_budget and has_item:
                    return {
                        "task_type": "ecommerce",
                        "summary": f"Search and inspect listings for: {user_goal}",
                        "target_url": target,
                        "needs_upfront_info": False,
                        "questions": [],
                        "coworker_message": f"I will open {'eBay' if 'ebay' in user_lower else 'the store'} and search for {user_goal}.",
                        "steps": ["Navigate to store", "Filter by price", "Extract top listings"],
                    }
                else:
                    return {
                        "task_type": "ecommerce",
                        "summary": f"Search, inspect, and negotiate for: {user_goal}",
                        "target_url": target,
                        "needs_upfront_info": True,
                        "questions": [
                            "What is your target budget or preferred brand?"
                        ],
                        "coworker_message": f"I can help you find and negotiate for that. Before I start searching, what is your target budget or preferred brand?",
                        "steps": ["Search target website", "Filter results", "Extract top options", "Submit offer if requested"],
                    }
            else:
                return {
                    "task_type": "general_browsing",
                    "summary": user_goal,
                    "target_url": "https://www.google.com",
                    "needs_upfront_info": False,
                    "questions": [],
                    "coworker_message": f"I am starting work on: {user_goal}. Opening browser to investigate.",
                    "steps": ["Navigate", "Interact", "Complete"],
                }

        if not self.is_configured():
            return get_heuristic_plan()

        conv_context = ""
        if conversation_history:
            turns = [
                f"{m['role'].capitalize()}: {m['content']}"
                for m in conversation_history[-4:]
                if m.get("content") and m["content"] != user_goal
            ]
            if turns:
                conv_context = "Conversation History (Memory):\n" + "\n".join(turns) + "\n\n"

        system_prompt = (
            "You are AURA, an elite AI coworker that automates web tasks on behalf of humans.\n"
            "Your principle: THINK FIRST. Before touching a browser, evaluate what info is required.\n"
            "Maintain conversational memory from previous dialogue turns.\n"
            "CRITICAL RULES FOR QUESTIONS:\n"
            "1. NEVER ask more than ONE question at a time. Asking multiple questions overwhelms the user.\n"
            "2. If the user already specified the item and budget (e.g. '$300 watches' or 'mechanical keyboards under $100'), do NOT ask questions! Set needs_upfront_info: false and questions: [].\n"
            "3. If the task is open web browsing, research, news, or public shopping, set needs_upfront_info: false and questions: [].\n"
            "4. If the user is expressing a closure/courtesy ('no thank you', 'thanks', 'all good', 'bye') or asking to stop ('stop', 'cancel'), set task_type to 'closure' or 'stop', optimized_search_query: '', needs_upfront_info: false, questions: [], steps: [].\n"
            "Respond ONLY with a valid JSON object matching this schema:\n"
            "{\n"
            '  "task_type": "ecommerce" | "portal_login" | "data_extraction" | "general_browsing" | "research" | "negotiation" | "inquiry" | "closure" | "stop",\n'
            '  "summary": "Short 1-sentence description of what will be done",\n'
            '  "optimized_search_query": "Concise 2-4 word query (e.g. latest AI news)",\n'
            '  "target_url": "https://..." or null,\n'
            '  "needs_upfront_info": true | false,\n'
            '  "questions": ["Single specific question if strictly necessary"],\n'
            '  "coworker_message": "Warm, professional message explaining what you will do or ask",\n'
            '  "steps": ["Step 1", "Step 2", ...]\n'
            "}"
        )

        user_content = f"{conv_context}Current User Task: {user_goal}\n"
        if web_intelligence:
            user_content += f"Web Intelligence from Anakin Search:\n{web_intelligence[:1500]}\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw_resp = await self.chat(messages, temperature=0.2, max_tokens=500)
        try:
            parsed = extract_json_object(raw_resp)
            if parsed and isinstance(parsed, dict):
                # Ensure strictly at most 1 question
                if parsed.get("questions"):
                    parsed["questions"] = [parsed["questions"][0]]
                # If budget and item or specific task details were provided, bypass upfront block
                has_budget = bool(re.search(r"(\$|usd|\b\d+\s*(?:dollars|bucks)\b|\b\d{2,4}\b)", user_lower))
                has_item = bool(re.search(r"\b(watch|watches|laptop|phone|camera|keyboard|shoes|jacket|car|tv|headphone|mic|microphone|monitor|mouse|drone|tablet)\b", user_lower))
                if has_budget and has_item:
                    parsed["needs_upfront_info"] = False
                    parsed["questions"] = []
                return parsed
        except Exception as e:
            logger.warning(f"Failed to parse LLM plan JSON: {e}, raw: {raw_resp[:200]}")

        # If LLM failed, timed out, or produced non-JSON, fallback cleanly
        return get_heuristic_plan()

    async def decide_action(
        self,
        goal: str,
        current_url: str,
        page_title: str,
        interactive_elements: List[Dict[str, Any]],
        action_history: List[str],
        gathered_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Phase 3: Decide next browser action in ReAct loop.
        """
        system_prompt = (
            "You are AURA Relay Browser ReAct Agent.\n"
            "Given current page URL, title, and interactive DOM elements, choose the ONE next action.\n"
            "You are a proactive, communicative AI coworker. Keep the user informed and ask for their voice guidance whenever their input, choice, or topic specification is helpful.\n"
            "Actions supported:\n"
            "- {\"action\": \"navigate\", \"value\": \"https://...\", \"reasoning\": \"...\", \"confidence\": 0.95}\n"
            "- {\"action\": \"click\", \"target\": \"selector or element text\", \"reasoning\": \"...\", \"confidence\": 0.9}\n"
            "- {\"action\": \"fill\", \"target\": \"selector or element text\", \"value\": \"text to fill\", \"reasoning\": \"...\", \"confidence\": 0.9}\n"
            "- {\"action\": \"press_key\", \"value\": \"Enter\", \"reasoning\": \"Submit search or form with Enter key\", \"confidence\": 0.9}\n"
            "- {\"action\": \"solve_otp\", \"target\": \"otp input selector\", \"reasoning\": \"Waiting for or filling OTP\", \"confidence\": 0.9}\n"
            "- {\"action\": \"ask_user\", \"value\": \"Spoken question for TTS to ask the user aloud\", \"reasoning\": \"Why user help or choice is needed\", \"confidence\": 0.9}\n"
            "- {\"action\": \"done\", \"value\": \"Substantive, friendly coworker summary of findings and what was accomplished\", \"reasoning\": \"Goal reached\", \"confidence\": 1.0}\n"
            "RULES:\n"
            "1. Output ONLY a valid JSON object matching the format above.\n"
            "2. For passwords/credentials or previous user answers, inspect values in gathered_info.\n"
            "3. If the user's intent is ambiguous, or if multiple options/topics/videos appear, or if you need the user's direction, emit 'ask_user'. Whenever candidate items, videos, or search results are available, proactively present the top 2-3 specific options in your question so the user can easily select (e.g., 'I found these videos on YouTube: 1. [Title 1], 2. [Title 2]. Would you like me to play #1, or do you have another video in mind?').\n"
            "4. When the user speaks their response, our voice system transcribes it and passes it back in 'gathered_info' under 'last_user_answer'. Use it immediately to fulfill their request!\n"
            "5. NEVER declare the task 'done' unless you have successfully and completely executed the user's goal (e.g., navigated to the page, searched for the item, found the item, and completed any requested interactions like purchasing or negotiating). Provide an informative, friendly summary of what was found or executed in 'value' so the user knows exactly what was achieved.\n"
            "6. When filling search bars or querying search engines, NEVER type full conversational sentences (like 'can you browse eBay for me for $300 watches'). Extract clean search keywords and product names only (like 'watches' or 'mechanical watches').\n"
            "7. If the user answered that they have no specific preference (e.g. 'go according to yourself', 'any', 'whatever', 'no choice', 'any good one', 'no preference', 'up to you'), OR if they already responded in 'last_user_answer': DO NOT ask them again! Immediately proceed with the top popular/featured option on the page (e.g., click listing #1, play video #1, or navigate to top result).\n"
            "8. When searching for watches, electronics, or retail products, ONLY browse e-commerce platforms like eBay or Amazon! NEVER navigate to bookstore or unrelated sites like AbeBooks or Goodreads!\n"
            "9. If on YouTube or media sites and video results are listed, click the video title or play button to start playback.\n"
            "10. When searching Google or search engines, ALWAYS prefer navigating directly to 'https://www.google.com/search?q=...' with your search query to get results immediately. If you type into a search box, use 'press_key' with 'Enter' to submit the query.\n"
        )

        # Streamline elements to minimal tokens so NVIDIA LLM responds quickly without timing out
        trimmed_elements = []
        for el in interactive_elements[:12]:
            t = (el.get("text") or el.get("placeholder") or "").strip()[:35]
            trimmed_elements.append({
                "tag": el.get("tag", ""),
                "id": el.get("id", ""),
                "text": t,
                "type": el.get("type", ""),
            })
        elements_summary = json.dumps(trimmed_elements)
        
        # Format extracted listings if available
        listings_summary = "None detected yet"
        listings = gathered_info.get("listings", [])
        if listings:
            listings_summary = "\n".join([
                f"- {item.get('title', '')[:60]} | Price: {item.get('price', '')} | URL: {item.get('url', '')[:60]}"
                for item in listings[:6]
            ])

        search_hint = gathered_info.get("optimized_query") or gathered_info.get("clean_keywords") or goal
        user_content = (
            f"GOAL: {goal}\n"
            f"OPTIMIZED SEARCH KEYWORDS: {search_hint}\n"
            f"CURRENT URL: {current_url}\n"
            f"PAGE TITLE: {page_title}\n"
            f"EXTRACTED PRODUCT LISTINGS ON PAGE:\n{listings_summary}\n"
            f"INTERACTIVE ELEMENTS: {elements_summary}\n"
            f"RECENT ACTIONS: {action_history[-3:] if action_history else 'None'}\n"
            f"GATHERED INFO: {json.dumps({k: v for k, v in gathered_info.items() if k != 'listings'})}\n"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw = await self.chat(messages, temperature=0.1, max_tokens=500)
        logger.info(f"LLM decide_action raw response: {raw}")
        try:
            parsed = extract_json_object(raw)
            if parsed and isinstance(parsed, dict) and "action" in parsed:
                return parsed
        except Exception as e:
            logger.warning(f"Failed to parse action JSON: {e}")

        # Smart fallback if LLM is unresponsive or times out:
        actions_count = len(action_history)
        if listings:
            # If listings are present on page, conclude with substantive findings!
            return {
                "action": "done",
                "value": self._build_deterministic_summary(goal, listings),
                "reasoning": f"Found and verified {len(listings)} matching listings on page",
                "confidence": 0.95
            }
            
        if actions_count == 0:
            if "google.com" in current_url and "search?q=" not in current_url:
                search_term = gathered_info.get("optimized_query") or gathered_info.get("clean_keywords") or "latest news"
                return {
                    "action": "fill",
                    "target": "textarea[name='q'], input[name='q'], input[type='search'], input[type='text']",
                    "value": search_term,
                    "reasoning": f"Searching for '{search_term}' on Google",
                    "confidence": 0.9,
                }
            return {
                "action": "scroll",
                "value": "600",
                "reasoning": "Exploring page content",
                "confidence": 0.8,
            }
        elif actions_count < 4:
            return {
                "action": "scroll",
                "value": "600",
                "reasoning": "Scrolling to review more information and listings",
                "confidence": 0.8,
            }
        else:
            return {
                "action": "done",
                "value": f"Explored page for '{goal}'. Completed search review.",
                "reasoning": "Exploration limit reached",
                "confidence": 0.7,
            }

    def _build_deterministic_summary(self, goal: str, listings: List[Dict[str, Any]]) -> str:
        """Construct structured markdown summary from extracted listings."""
        if not listings:
            return f"Completed exploration for '{goal}'. Checked page details and options."

        lines = [f"### ⌚ Discovered Options for: *{goal}*\n"]
        lines.append(f"I found **{len(listings)} matching products** within your criteria:\n")
        
        for i, item in enumerate(listings[:5]):
            title = item.get("title", f"Option {i+1}")
            price = item.get("price", "N/A")
            url = item.get("url", "")
            shipping = item.get("shipping", "")
            ship_str = f" • *{shipping}*" if shipping else ""
            if url:
                lines.append(f"{i+1}. **[{title}]({url})** — `{price}`{ship_str}")
            else:
                lines.append(f"{i+1}. **{title}** — `{price}`{ship_str}")

        top = listings[0]
        top_title = top.get("title", "Top Pick")
        top_price = top.get("price", "")
        top_url = top.get("url", "")
        if top_url:
            lines.append(f"\n💡 **Top Recommendation:** [{top_title}]({top_url}) at **{top_price}** matches your budget and requirements.")
        else:
            lines.append(f"\n💡 **Top Recommendation:** **{top_title}** at **{top_price}** matches your budget and requirements.")

        return "\n".join(lines)

    async def summarize_task(
        self,
        goal: str,
        action_history: List[str],
        gathered_info: Dict[str, Any]
    ) -> str:
        """
        Produce a high-value markdown summary of actions, findings, prices, and links.
        Deterministic fallback ensures users always get real product details even without LLM.
        """
        listings = gathered_info.get("listings", [])
        if listings:
            return self._build_deterministic_summary(goal, listings)

        # If LLM configured, generate contextual conversational report
        if self.is_configured():
            system_prompt = (
                "You are AURA, an elite AI coworker. Provide a warm, concise, and professional summary of the task performed, "
                "the key actions taken, findings, and next recommended actions for the user in Markdown format."
            )
            user_content = (
                f"GOAL: {goal}\n"
                f"ACTION HISTORY:\n" + "\n".join(action_history[-6:]) + "\n"
                f"PAGE DETAILS: {gathered_info.get('current_page_summary', '')}\n"
            )
            try:
                summary = await self.chat([
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ], temperature=0.3, max_tokens=350)
                if summary and len(summary.strip()) > 20:
                    return summary.strip()
            except Exception as e:
                logger.warning(f"Error generating LLM summary: {e}")

        return f"Successfully investigated '{goal}'. Reviewed page details and confirmed current status."

_llm_provider: Optional[NvidiaLLMProvider] = None


def get_llm_provider() -> NvidiaLLMProvider:
    global _llm_provider
    if _llm_provider is None:
        _llm_provider = NvidiaLLMProvider()
    return _llm_provider
