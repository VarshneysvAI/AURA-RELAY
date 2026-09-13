"""
AURA Relay Planner - Task planning and step generation
"""
from typing import List, Dict, Any


class Planner:
    """Generates execution plans for tasks."""
    
    # Default plan for secure portal document retrieval
    SECURE_PORTAL_PLAN = [
        "Open the secure portal login page",
        "Fill username field",
        "Fill password field", 
        "Click login button",
        "Wait for OTP requirement detection",
        "Navigate to email inbox",
        "Find OTP email",
        "Extract OTP code",
        "Return to secure portal",
        "Fill OTP field",
        "Submit OTP verification",
        "Wait for dashboard navigation",
        "Identify available document options",
        "Ask user which document to download if ambiguous",
        "Click selected document button",
        "Verify download/completion",
        "Report success with evidence"
    ]
    
    def __init__(self):
        self._custom_plans: Dict[str, List[str]] = {}
    
    def get_plan_for_instruction(self, instruction: str) -> List[str]:
        """
        Get a plan based on the user instruction.
        
        Currently uses keyword matching. In future, could use LLM.
        """
        instruction_lower = instruction.lower()
        
        # Check for secure portal task
        if any(kw in instruction_lower for kw in [
            "secure portal", "login", "tax document", "otp", "retrieve"
        ]):
            return self.SECURE_PORTAL_PLAN.copy()
        
        # Default generic plan
        return self._generate_generic_plan(instruction)
    
    def _generate_generic_plan(self, instruction: str) -> List[str]:
        """Generate a generic plan for unknown instructions."""
        return [
            "Analyze the user instruction",
            "Navigate to target URL or search",
            "Identify required actions",
            "Execute actions step by step",
            "Handle any authentication if required",
            "Complete the requested task",
            "Verify completion",
            "Report results"
        ]
    
    def register_plan(self, name: str, steps: List[str]) -> None:
        """Register a custom plan."""
        self._custom_plans[name] = steps
    
    def get_plan_by_name(self, name: str) -> List[str]:
        """Get a plan by name."""
        return self._custom_plans.get(name, []).copy()
