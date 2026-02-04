import os
from google import genai
from google.genai import types

def generate(system_prompt, user_prompt):
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
