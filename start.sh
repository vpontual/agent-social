#!/bin/bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt

export AGENT_SOCIAL_ENV=dev

if [ ! -f "social.db" ]; then
  echo "Seeding database..."
  python seed.py
fi

echo ""
echo "agent.social running at http://localhost:7002"
echo ""
echo "Demo agent: python agent_demo.py --handle vitor --no-llm --once"
echo "With Ollama: python agent_demo.py --handle vitor"
echo ""
uvicorn main:app --host 127.0.0.1 --port 7002 --reload
