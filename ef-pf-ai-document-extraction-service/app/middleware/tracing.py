from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from fastapi import Request, Response
import re
import uuid
import json
from contextvars import ContextVar

root_trace_ctx: ContextVar[str] = ContextVar("root_trace_id", default=None)

def _format_srn_as_trace_id(srn: str) -> str:
    """
    Formats a Service Request Number (SRN) into a W3C-compliant 32-character hex trace ID.
    - If longer than 32 chars, it's truncated.
    - If shorter than 32 chars, it's right-padded with '0's.
    """
    # Ensure SRN is a string and remove any non-hex/non-S characters for safety
    temp = str(srn).lower().replace('s', 'a')
    safe_srn = re.sub(r'[^0-9a-f]', '', temp)
        
    if len(safe_srn) > 32:
        return safe_srn[:32]
    else:
        random_hex = uuid.uuid4().hex[:32 - len(safe_srn)]
        return safe_srn + random_hex

class W3CTracingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        root_trace = None

        # 1. Attempt to get SRN from request body for trace_id
        try:
            body_bytes = await request.body()
            request._body = body_bytes  # Restore body for endpoint
            if body_bytes:
                data = json.loads(body_bytes)
                # Correctly access the nested srn_no
                srn = data.get("request", {}).get("srn_no")
                if srn:
                    root_trace = _format_srn_as_trace_id(srn)
        except (json.JSONDecodeError, Exception):
            pass # Proceed if body isn't valid JSON or srn_no is missing

        # 2. If no SRN, check for incoming traceparent header
        if not root_trace:
            traceparent = request.headers.get("traceparent", "")
            if traceparent:
                match = re.match(r"00-([0-9a-f]{32})-([0-9a-f]{16})-01", traceparent)
                if match:
                    root_trace = match.group(1)

        # 3. If still no trace ID, generate a new one
        if not root_trace:
            root_trace = uuid.uuid4().hex
            
        # 4. Set thread-local context and state
        token = root_trace_ctx.set(root_trace)
        request.state.root_trace_id = root_trace
        
        response: Response = await call_next(request)
        
        # 5. Inject traceparent back for chaining
        span_id = uuid.uuid4().hex[:16]
        response.headers["traceparent"] = f"00-{root_trace}-{span_id}-01"
        
        root_trace_ctx.reset(token)
        return response
