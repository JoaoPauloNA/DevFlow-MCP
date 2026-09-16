#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_v12_declarative_routing import TestDeclarativeRouting
from tests.test_devflow import (
    test_exact_model_matrix,
    test_json_envelope_accepts_model_trailing_text,
    test_unknown_vault_needs_user_without_creating_a_folder
)

def run_all():
    print("========================================")
    print(" DevFlow MCP Test Suite (v1.2.0)")
    print("========================================")
    
    # 1. Legacy Fast Tests
    legacy_fast = [
        test_exact_model_matrix,
        test_json_envelope_accepts_model_trailing_text,
        test_unknown_vault_needs_user_without_creating_a_folder
    ]
    print("\n--- Executando Testes Legados v1.1 ---")
    for t in legacy_fast:
        t()
        print(f"PASS {t.__name__}")
        
    # 2. Declarative Routing Test Suite
    print("\n--- Executando Suíte Declarative Routing v1.2 ---")
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDeclarativeRouting)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    total = len(legacy_fast) + result.testsRun
    failures = len(result.failures) + len(result.errors)
    
    print("\n========================================")
    print(f"Total executado: {total} testes")
    print(f"Sucessos: {total - failures}")
    print(f"Falhas: {failures}")
    print("========================================")
    
    if not result.wasSuccessful():
        sys.exit(1)

if __name__ == '__main__':
    run_all()
