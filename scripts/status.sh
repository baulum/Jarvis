#!/usr/bin/env bash
cd "$(dirname "$0")/../core" && exec uv run --frozen jarvisctl status
