"""
AURA Relay Smoke Test Script
Tests the complete flow from start to finish.
"""
import sys
import time
import httpx
import asyncio

BASE_URL = "http://127.0.0.1:8000"


def wait_for_health(timeout=30):
    """Wait for the server to be healthy."""
    print("Waiting for server health check...")
    start = time.time()
    
    while time.time() - start < timeout:
        try:
            response = httpx.get(f"{BASE_URL}/health", timeout=5)
            if response.status_code == 200:
                data = response.json()
                print(f"✓ Server healthy: {data}")
                return True
        except Exception as e:
            print(f"  Health check pending... ({e})")
        
        time.sleep(1)
    
    print("✗ Server health check timed out")
    return False


def run_smoke_test():
    """Run the smoke test."""
    print("\n" + "="*60)
    print("AURA Relay Smoke Test")
    print("="*60 + "\n")
    
    # Step 1: Wait for health
    if not wait_for_health():
        return False
    
    # Step 2: Start the agent
    print("\nStarting agent with instruction...")
    instruction = "Log into the secure portal, complete OTP verification, and retrieve the tax document."
    
    try:
        response = httpx.post(
            f"{BASE_URL}/api/start",
            json={"instruction": instruction},
            timeout=10
        )
        
        if response.status_code != 200:
            print(f"✗ Failed to start agent: {response.text}")
            return False
        
        print("✓ Agent started successfully")
    except Exception as e:
        print(f"✗ Error starting agent: {e}")
        return False
    
    # Step 3: Poll state until question appears
    print("\nWaiting for agent to ask question...")
    question_found = False
    question_id = None
    
    for i in range(60):  # Wait up to 60 seconds
        time.sleep(2)
        
        try:
            response = httpx.get(f"{BASE_URL}/api/state", timeout=10)
            state = response.json()
            
            status = state.get("status", "")
            print(f"  Status: {status}, Step: {state.get('current_step', '-')[:40]}")
            
            if state.get("question"):
                question_found = True
                question_id = state.get("question_id")
                print(f"✓ Question found: {state['question']}")
                break
            
            if status in ("done", "error", "stopped"):
                print(f"! Agent finished without question (status: {status})")
                break
                
        except Exception as e:
            print(f"  Error polling state: {e}")
    
    if not question_found:
        print("✗ No question was asked by agent")
        # Check if completed anyway
        try:
            response = httpx.get(f"{BASE_URL}/api/state", timeout=10)
            state = response.json()
            if state.get("status") == "done":
                print("✓ But task completed successfully!")
                return True
        except:
            pass
        return False
    
    # Step 4: Answer the question
    print("\nAnswering question with 'yes'...")
    
    try:
        response = httpx.post(
            f"{BASE_URL}/api/answer",
            json={"answer": "yes"},
            timeout=10
        )
        
        if response.status_code == 200:
            print("✓ Answer submitted successfully")
        else:
            print(f"✗ Failed to submit answer: {response.text}")
            return False
    except Exception as e:
        print(f"✗ Error submitting answer: {e}")
        return False
    
    # Step 5: Wait for completion
    print("\nWaiting for task completion...")
    
    for i in range(30):  # Wait up to 30 seconds
        time.sleep(2)
        
        try:
            response = httpx.get(f"{BASE_URL}/api/state", timeout=10)
            state = response.json()
            
            status = state.get("status", "")
            print(f"  Status: {status}")
            
            if status == "done":
                print(f"\n✓ Task completed successfully!")
                print(f"  Result: {state.get('result', 'N/A')[:100]}")
                
                # Verify screenshots exist
                screenshots = state.get("screenshots", [])
                if screenshots:
                    print(f"  Screenshots captured: {len(screenshots)}")
                else:
                    print("  Warning: No screenshots captured")
                
                return True
            
            if status == "error":
                print(f"\n✗ Task failed with error: {state.get('error_message', 'Unknown')}")
                return False
                
        except Exception as e:
            print(f"  Error polling state: {e}")
    
    print("\n✗ Completion timed out")
    return False


if __name__ == "__main__":
    success = run_smoke_test()
    
    print("\n" + "="*60)
    if success:
        print("SMOKE TEST PASSED ✓")
    else:
        print("SMOKE TEST FAILED ✗")
    print("="*60 + "\n")
    
    sys.exit(0 if success else 1)
