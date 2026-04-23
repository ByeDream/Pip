"""Throwaway import + instantiation sanity check for Tier 1 code."""
from pip_agent.streaming_session import StreamingSession, StaleSessionError
from pip_agent.agent_host import AgentHost
from pip_agent.config import settings

print("imports OK")
print("enable_streaming_session:", settings.enable_streaming_session)
print("stream_idle_ttl_sec:", settings.stream_idle_ttl_sec)
print("stream_max_live:", settings.stream_max_live)
print("AgentHost._run_turn_streaming:", hasattr(AgentHost, "_run_turn_streaming"))
print("AgentHost._idle_sweep_loop:", hasattr(AgentHost, "_idle_sweep_loop"))
print("AgentHost.close_all_streaming_sessions:", hasattr(AgentHost, "close_all_streaming_sessions"))
print("StreamingSession:", StreamingSession.__name__)
print("StaleSessionError:", StaleSessionError.__name__)
