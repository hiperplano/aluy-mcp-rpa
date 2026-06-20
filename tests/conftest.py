"""Põe a raiz do repo no sys.path para `import src.<mod>` funcionar no pytest."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
