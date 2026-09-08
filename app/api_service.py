import os
from google import genai
from google.genai import types
from app.metrics import track_call

def generate(system_prompt, user_prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

    with track_call("generate"):
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"]
            )
        )
    return response.text.strip() if response.text else ""
