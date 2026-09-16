# Tiny sidecar: reads Claude Code transcripts, exposes claude_session_info.
# Stdlib only — no pip install, no build step.
FROM python:3.12-slim
WORKDIR /app
COPY labeler.py .
EXPOSE 9105
ENTRYPOINT ["python", "-u", "labeler.py"]
