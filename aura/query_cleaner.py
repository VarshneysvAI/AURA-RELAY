"""
AURA Relay — Query & Intent Normalization Utility
Extracts clean search keywords, price constraints, and platform-specific URLs
from raw conversational user instructions.
"""

import re
import urllib.parse
from typing import Dict, Any, Optional


def build_search_url(platform: str, query: str, max_price: Optional[int] = None) -> str:
    """Constructs a clean, platform-specific search URL using optimized search keywords."""
    clean_q = (query or "").strip()
    # Strip site name and price filter phrases from the keyword itself so eBay finds products
    clean_q = re.sub(r"(?i)\b(ebay|amazon|google|wikipedia|youtube)\b", " ", clean_q)
    clean_q = re.sub(r"(?i)\b(under|below|less than|max)\s+\$?\d+\b", " ", clean_q)
    clean_q = re.sub(r"\s+", " ", clean_q).strip() or "trending items"
    encoded = urllib.parse.quote(clean_q)
    plat = (platform or "google").lower()

    if plat == "ebay":
        if max_price:
            return f"https://www.ebay.com/sch/i.html?_nkw={encoded}&_udhi={max_price}"
        return f"https://www.ebay.com/sch/i.html?_nkw={encoded}"
    elif plat == "amazon":
        return f"https://www.amazon.com/s?k={encoded}"
    elif plat == "wikipedia":
        return f"https://en.wikipedia.org/w/index.php?search={encoded}"
    elif plat == "youtube":
        return f"https://www.youtube.com/results?search_query={encoded}"
    elif plat == "github":
        return f"https://github.com/search?q={encoded}"
    else:
        return f"https://www.google.com/search?q={encoded}"


def clean_search_query(text: str) -> Dict[str, Any]:
    """
    Cleans raw conversational instructions (e.g. 'do one thing a watches which are professional I don t have a target budget more priority the brand can be seafood')
    into structured search parameters:
    - platform: 'ebay' | 'amazon' | 'wikipedia' | 'youtube' | 'google' | 'github'
    - keywords: 'Seiko professional watches'
    - max_price: Optional[int]
    - target_url: 'https://www.ebay.com/sch/i.html?_nkw=Seiko%20professional%20watches'
    - anakin_query: 'Seiko professional watches'
    """
    text_clean = text.strip()
    text_lower = text_clean.lower()

    # 1. Explicit URL & Platform Detection
    url_match = re.search(r"https?://[^\s'\"]+", text_clean)
    explicit_url = url_match.group(0).rstrip(".,;)\"'") if url_match else None

    if explicit_url and ("sandbox" in explicit_url or "127.0.0.1" in explicit_url or "localhost" in explicit_url):
        return {
            "platform": "portal",
            "keywords": "secure portal",
            "max_price": None,
            "target_url": explicit_url,
            "anakin_query": "secure portal",
        }

    if "sandbox" in text_lower or "portal" in text_lower or "login" in text_lower and not any(w in text_lower for w in ["ebay", "amazon", "shop", "buy"]):
        platform = "portal"
    elif "amazon" in text_lower:
        platform = "amazon"
    elif "wikipedia" in text_lower or "wiki" in text_lower:
        platform = "wikipedia"
    elif "youtube" in text_lower or "video" in text_lower:
        platform = "youtube"
    elif "github" in text_lower:
        platform = "github"
    elif any(w in text_lower for w in ["buy", "shop", "price", "order", "watch", "watches", "laptop", "phone", "keyboard", "deal", "ebay"]):
        platform = "ebay"
    else:
        platform = "google"

    # 2. Extract Price / Budget Constraints
    max_price: Optional[int] = None
    # If the user explicitly stated they don't have a budget, ignore price numbers in that context
    has_no_budget_claim = bool(re.search(
        r"(?i)\b(i don'?t have (?:a|any)? target budget|no target budget|no budget|don'?t have (?:a|any)? budget|without (?:a|any)? budget|budget (?:doesn'?t matter|is not an issue))\b",
        text_clean
    ))

    if not has_no_budget_claim:
        price_patterns = [
            r"(?:under|below|less than|max(?:imum)?|budget(?:\s*of)?|for)?\s*(?:\$|usd\s*)(\d+(?:\.\d+)?)\b",
            r"(?:under|below|less than|max)\s*(\d+(?:\.\d+)?)\b",
            r"\b(\d+)\s*(?:dollars|bucks)\b",
        ]
        for pattern in price_patterns:
            match = re.search(pattern, text_lower)
            if match:
                try:
                    val = float(match.group(1))
                    if val > 0:
                        max_price = int(val)
                        break
                except (ValueError, TypeError):
                    continue

    # 3. Clean Conversational Noise & Voice Preamble
    cleaned = text_clean

    # Strip speech acknowledgements / conversational reaction preamble
    cleaned = re.sub(
        r"(?i)\b(okay|ok|alright|sure|yeah|yes)?\s*(opening browser is nice|opening browser|that is nice|nice|great|good)\s*(but|and)?\b",
        " ",
        cleaned,
    )

    # Conversational imperatives / openers: "do one thing", "do me a favor", "let's do one thing"
    cleaned = re.sub(
        r"(?i)\b(can you )?(please )?(do one thing|do me a favor|do something like|do something|let'?s do one thing|you do one thing|what you can do is|here is what you do)\b",
        " ",
        cleaned,
    )

    # Conversational budget disclaimers: "I don't have a target budget", "no target budget", etc.
    cleaned = re.sub(
        r"(?i)\b(i don'?t have (?:a|any)? target budget|i don'?t have (?:a|any)? budget|no target budget|no budget|target budget|budget limit|budget constraints?|without (?:a|any)? budget|budget (?:doesn'?t matter|is not an issue)|any budget)\b",
        " ",
        cleaned,
    )

    # Conversational priority / preference meta-phrases
    cleaned = re.sub(
        r"(?i)\b(more priority|highest priority|first priority|top priority|give priority to|priority is|prioritize|priority)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(the brand can be|the brand should be|brand can be|brand should be|brand could be|the brand is|brand is|brand would be|the brand|brand)\b",
        " ",
        cleaned,
    )

    # Fix common speech-to-text acoustic misrecognitions
    # In watch / shopping context: "seafood" -> "Seiko"
    is_watch_context = bool(re.search(r"(?i)\b(watch|watches|timepiece|chronograph)\b", cleaned))
    if is_watch_context:
        cleaned = re.sub(r"(?i)\b(seafood|see food|sayko|seco|see co|psycho)\b", "Seiko", cleaned)
        cleaned = re.sub(r"(?i)\b(roll x|raw lex|rawlex)\b", "Rolex", cleaned)
        cleaned = re.sub(r"(?i)\b(o mega|omg)\b", "Omega", cleaned)
        cleaned = re.sub(r"(?i)\b(tissue|ti so)\b", "Tissot", cleaned)
    else:
        # If "seafood" appears with brand/shop wording, still map to Seiko
        if re.search(r"(?i)\b(brand|shop|buy)\b", text_clean) and re.search(r"(?i)\b(seafood)\b", cleaned):
            cleaned = re.sub(r"(?i)\bseafood\b", "Seiko", cleaned)

    cleaned = re.sub(r"(?i)\b(proud|proude|prowd)\b", "provide", cleaned)

    # Grammar cleanups from speech-to-text: "a watches" -> "watches", "a laptops" -> "laptops"
    cleaned = re.sub(r"(?i)\ba\s+(watches|laptops|smartphones|phones|keyboards|shoes|cars|items|products)\b", r"\1", cleaned)

    # Remove relative pronouns & copula: "which are", "which is", "that are", "that is", "who are"
    cleaned = re.sub(r"(?i)\b(which are|which is|that are|that is|who are|who is)\b", " ", cleaned)

    # Remove conversational politeness & requests
    cleaned = re.sub(
        r"(?i)\b(can you|could you|would you|please|will you|go and|i want to|i'd like to|help me|help me with)\b",
        " ",
        cleaned,
    )

    # Remove conversational meta-instructions and search lead-ins
    cleaned = re.sub(
        r"(?i)\b(browse the web|browse the internet|browse online|browse web|surf the web|the web)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(see something new about|see something about|see about|provide some|show me some|show me|find some|get some)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(in a \w+ way possible|in the \w+ way possible|as \w+ as possible|faster way possible|fastest way possible)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(to search about the|to search about|to search for the|to search for|to search on|to search|to look up the|to look up|to find me|to find|to browse)\b",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(the wikipedia on|wikipedia on|wiki on|google on|ebay on|amazon on)\b",
        " ",
        cleaned,
    )

    # Remove platform names from keyword query
    cleaned = re.sub(r"(?i)\b(ebay|amazon|wikipedia|wiki|google|github)\b", " ", cleaned)

    # Remove action verbs and conversational fillers
    cleaned = re.sub(
        r"(?i)\b(search about the|search about|search for the|search for|search on|search|browse for me for|browse for me|browse for|browse to|browse|look for the|look for|look up the|look up|find me|find|buy|shop for|shop|articles? on|articles? about|articles?|info about|information on|information about)\b",
        " ",
        cleaned,
    )

    # Normalize 'latest news about X' or 'news about X'
    cleaned = re.sub(r"(?i)\b(latest\s+news\s+about|news\s+about)\b", "latest news ", cleaned)

    # Strip price phrase from keywords so it doesn't pollute the search query
    if max_price is not None:
        cleaned = re.sub(r"(?i)(?:under|below|less than|max|budget of|for)?\s*(\$|usd)?\s*" + str(max_price) + r"(\s*(?:dollars|bucks))?", " ", cleaned)
        cleaned = re.sub(r"\$\s*\d+", " ", cleaned)

    # Strip conversational noise words and pronouns
    cleaned = re.sub(
        r"(?i)\b(no as such|not more features|features|will be good one|a good|the best|good one|some good|any good|just|me|for|on|in|at|but|and|as|or|to|of|with|user preference|can be|should be|could be|have a|have|don t|dont|i|i'm|my|mine|you|your|we|our|us|they|them|it|its|thing|one thing)\b",
        " ",
        cleaned,
    )

    # Strip leading/trailing prepositions and determiners (e.g. 'on AI researchers' -> 'AI researchers')
    cleaned = re.sub(r"(?i)^\s*(about|on|regarding|of|the|a|an)\s+", "", cleaned.strip())

    # Strip remaining special characters and collapse spaces
    cleaned = re.sub(r"[^\w\s-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Reorder keywords if recognized brand is present to ensure natural search order (e.g. "watches professional Seiko" -> "Seiko professional watches")
    known_brands = ["seiko", "rolex", "omega", "casio", "citizen", "tissot", "apple", "samsung", "asus", "dell", "sony", "logitech"]
    words = cleaned.split()
    found_brand = None
    other_words = []
    for w in words:
        if w.lower() in known_brands and not found_brand:
            found_brand = w.capitalize()
        else:
            other_words.append(w)
    if found_brand:
        # Move brand to front
        cleaned = f"{found_brand} {' '.join(other_words)}".strip()

    # Fallback to meaningful keyword if stripped too much
    if not cleaned:
        if "watch" in text_lower:
            cleaned = "watches"
        elif "laptop" in text_lower:
            cleaned = "laptops"
        elif "phone" in text_lower:
            cleaned = "smartphones"
        elif "keyboard" in text_lower:
            cleaned = "keyboards"
        else:
            cleaned = "trending items"

    target_url = build_search_url(platform, cleaned, max_price)

    # Construct clean query for Anakin search
    if max_price:
        anakin_query = f"{cleaned} under ${max_price}"
    else:
        anakin_query = cleaned

    return {
        "platform": platform,
        "keywords": cleaned,
        "max_price": max_price,
        "target_url": target_url,
        "anakin_query": anakin_query,
    }


def is_stop_intent(text: str) -> bool:
    """
    Returns True if the user is explicitly requesting to stop, cancel, abort, or halt.
    Prevents running search engine or browser tasks for commands like 'stop', 'please stop', 'cancel'.
    """
    if not text:
        return False
    t = text.strip().lower()
    t_clean = re.sub(r"[.!?,;:]+$", "", t).strip()

    exact_stops = {
        "stop", "stop it", "stop that", "stop now", "please stop", "stop please",
        "stop browsing", "stop searching", "stop working", "stop everything", "stop agent",
        "cancel", "cancel it", "cancel that", "cancel task", "please cancel", "cancel please",
        "halt", "abort", "quit", "pause", "nevermind", "never mind", "shut down",
        "don't do that", "dont do that", "do not do that", "hold on", "wait stop"
    }
    if t_clean in exact_stops:
        return True

    stop_pattern = r"^(?:can\s+you\s+)?(?:please\s+)?(?:stop|cancel|abort|halt|quit)\b(?:\s+(?:it|that|now|the\s+task|browsing|searching|running|doing\s+that))?$"
    return bool(re.match(stop_pattern, t_clean))


def is_conversational_closure(text: str) -> bool:
    """
    Returns True if the user is offering a polite conversational closing, courtesy, or dismissal
    (e.g., 'no thank you', 'no thanks', 'nothing else', 'that's all', 'i'm good', 'thanks', 'bye').
    Prevents searching Google / Anakin for phrases like 'no thank you'.
    """
    if not text:
        return False
    t = text.strip().lower()
    t_clean = re.sub(r"[.!?,;:]+$", "", t).strip()

    exact_closures = {
        "no", "no thank you", "no thanks", "no, thank you", "no, thanks", "no thank u", "no thanks!",
        "nope", "nah", "nothing", "nothing else", "nothing more", "nothing for now",
        "no need", "no more", "no that's all", "no that is all", "no that's it",
        "that's all", "that is all", "that's it", "that is it", "that will be all", "that'll be all",
        "i'm good", "im good", "i am good", "no i'm good", "no im good", "all good", "we are good", "we're good",
        "i'm done", "im done", "i am done", "we're done", "we are done",
        "thank you", "thanks", "thanks a lot", "thank you very much", "thanks so much", "thank u", "many thanks",
        "bye", "goodbye", "good bye", "see you", "see ya", "have a good one", "have a nice day",
        "great job", "awesome thanks", "perfect thank you", "ok thanks", "okay thanks",
        "done", "finished", "it's fine", "its fine", "no problem"
    }
    if t_clean in exact_closures:
        return True

    # If sentence contains action verbs like "search", "buy", "find", "open", "browse", "look", "show", it's NOT a closure
    action_keywords = ["search", "buy", "find", "open", "browse", "look", "show", "get", "ebay", "amazon", "google", "wiki", "youtube", "order"]
    if any(k in t_clean for k in action_keywords):
        return False

    closure_pattern = (
        r"^(?:no\s+)?(?:thank\s+you(?:\s+so\s+much|\s+very\s+much)?|thanks(?:\s+a\s+lot)?|"
        r"i'?m\s+good|all\s+good|nothing(?:\s+(?:else|more|for\s+now))?|"
        r"that(?:'?s|\s+is|\s+will\s+be)\s+(?:all|it|enough)|"
        r"goodbye|bye|nope|nah|done|that\s+is\s+fine|that'?s\s+fine)$"
    )
    return bool(re.match(closure_pattern, t_clean))


def is_conversational_inquiry(text: str) -> bool:
    """
    Returns True if the message is a conversational greeting or inquiry about capabilities.
    """
    if not text:
        return False
    t = text.strip().lower()
    t_clean = re.sub(r"[.!?,;:]+$", "", t).strip()

    greetings = {"hello", "hi", "hey", "good morning", "good afternoon", "good evening", "howdy", "sup"}
    if t_clean in greetings:
        return True

    return any(
        phrase in t_clean
        for phrase in [
            "listen", "hear me", "can you hear", "can you listen",
            "describe your work", "how do you work", "what can you do", "who are you",
            "what is aura", "help me understand"
        ]
    )


