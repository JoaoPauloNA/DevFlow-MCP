#!/usr/bin/env python3
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Regex patterns for sensitive tokens and keys
PATTERNS = [
    (r'(?i)(?:api_key|apikey|secret_key|secretkey|access_token|auth_token)\s*[:=]\s*["\']([a-zA-Z0-9_\-\.]{16,})["\']', 'API Key / Secret pattern'),
    (r'(?i)bearer\s+[a-zA-Z0-9_\-\.]{20,}', 'Bearer Token pattern'),
    (r'(?i)-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', 'Private Key Header'),
    (r'(?i)(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36}', 'GitHub Token pattern'),
    (r'(?i)cliproxy[A-Za-z0-9]*ApiKey\b', 'Legacy raw key literal'),
    (r'(?i)(?:password|passwd|pwd)\s*[:=]\s*["\'][^"\']{4,}["\']', 'Password pattern'),
    (r'(?i)sk-[A-Za-z0-9]{20,}', 'OpenAI secret key pattern')
]

# Paths/files to ignore in scan (already in .gitignore or safe schemas)
IGNORE_DIRS = {'.git', 'runtime', 'worktrees', '__pycache__', '.pytest_cache', 'backups'}
IGNORE_FILES = {'secret_scan.py', '.gitignore'}

def scan_file(file_path):
    issues = []
    try:
        text = file_path.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return issues

    for lineno, line in enumerate(text.splitlines(), 1):
        # Skip commented explanations or documentation mentions of placeholders
        if 'sua_chave_aqui' in line or 'sua-chave' in line or 'MOCK_KEY' in line or 'CANARY_KEY' in line or 'CLIPROXY_' in line:
            continue
        for pat, desc in PATTERNS:
            if re.search(pat, line):
                issues.append((lineno, desc))
    return issues

def main():
    print("========================================")
    print(" DevFlow Secret Scanner")
    print("========================================")
    
    total_scanned = 0
    found_violations = []

    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        for f in files:
            if f in IGNORE_FILES or f.endswith(('.pyc', '.png', '.sqlite', '.sqlite3', '.log')):
                continue
            fp = Path(root) / f
            total_scanned += 1
            issues = scan_file(fp)
            if issues:
                found_violations.append((fp.relative_to(ROOT), issues))

    print(f"Total de arquivos analisados: {total_scanned}")
    
    if found_violations:
        print("\n[ALERTA DE SEGURANÇA] Violações encontradas:")
        for fp, issues in found_violations:
            print(f"  - Arquivo: {fp}")
            for lineno, desc in issues:
                print(f"      Linha {lineno}: {desc}")
        print("\nSECRET_SCAN=FAIL")
        sys.exit(1)
    else:
        print("\nNenhum segredo ou credencial foi detectado em arquivos versionáveis.")
        print("SECRET_SCAN=PASS")
        sys.exit(0)

if __name__ == '__main__':
    main()
