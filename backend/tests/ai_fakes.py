"""Synthetic protocol server used by unit and browser tests; never accesses a network."""
import json
import httpx
from backend.app.ai_provider import Provider

class FakeProvider(Provider):
    def __init__(self):
        self.requests = []
        super().__init__(httpx.MockTransport(self.respond))

    def respond(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if body["model"] == "invalid-model" if "model" in body else "invalid-model" in str(request.url):
            return httpx.Response(401, json={"error": "synthetic failure"})
        if "input" in body:
            protocol = "responses"
            envelope = json.loads(body["input"][0]["content"][0]["text"])
        elif "contents" in body:
            protocol = "gemini"
            envelope = json.loads(body["contents"][0]["parts"][0]["text"])
        else:
            protocol = "anthropic" if "system" in body else "chat"
            envelope = json.loads(body["messages"][-1]["content"][0]["text"])
        image = any(m["content"].get("image_reference") for m in envelope["messages"])
        reply = "red" if image else "合成回复，尚未发送到微信"
        if envelope.get('reply_allowed') is False:
            reply = ''
        memory = envelope["memory"] + "\n" + "\n".join(m["content"].get("text") or "图片" for m in envelope["messages"]) + "\n"+reply
        output = json.dumps(dict(memory=memory, reply=reply), ensure_ascii=False)
        if protocol == "responses":
            result = dict(status="completed", output=[dict(type="message", content=[dict(type="output_text", text=output)])])
        elif protocol == "chat":
            result = dict(choices=[dict(finish_reason="stop", message=dict(content=output))])
        elif protocol == "anthropic":
            result = dict(stop_reason="end_turn", content=[dict(type="text", text=output)])
        else:
            result = dict(candidates=[dict(finishReason="STOP", content=dict(parts=[dict(text=output)]))])
        return httpx.Response(200, json=result)
