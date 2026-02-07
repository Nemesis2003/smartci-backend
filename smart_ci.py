#!/usr/bin/env python3
"""
Smart CI - Intelligent test selection for Python projects
Production-ready version with improved accuracy and error handling
"""
import argparse
import subprocess
import sys
import ast
import json
import re
from pathlib import Path
from typing import List, Dict, Set, Optional, Tuple

class SmartCI:
    def __init__(self, repo_path: str, base_sha: str, head_sha: str, test_command: str = "pytest"):
        self.repo_path = Path(repo_path)
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.test_command = test_command
        self.results = {
            'changed_files': [],
            'changed_functions': {},
            'selected_tests': [],
            'analysis_mode': None,
            'success': False,
            'error': None
        }
        
    def run(self) -> Dict:
        """Main execution - returns JSON results"""
        try:
            # 1. Get changed files
            changed_files = self.get_changed_files()
            if not changed_files:
                self.results['analysis_mode'] = 'no_changes'
                self.results['success'] = True
                return self.results
            
            self.results['changed_files'] = changed_files
            
            # 2. Analyze changes
            analysis = self.analyze_changes(changed_files)
            self.results['changed_functions'] = analysis
            
            # 3. Select tests
            selected_tests = self.select_tests(changed_files, analysis)
            self.results['selected_tests'] = selected_tests
            
            # 4. Determine analysis mode
            if not selected_tests:
                self.results['analysis_mode'] = 'no_tests_needed'
            elif len(selected_tests) == 1 and selected_tests[0] == 'ALL':
                self.results['analysis_mode'] = 'run_all'
            else:
                self.results['analysis_mode'] = 'smart_selection'
                
            self.results['success'] = True
            return self.results
            
        except Exception as e:
            self.results['error'] = str(e)
            self.results['analysis_mode'] = 'error_fallback'
            self.results['selected_tests'] = ['ALL']
            return self.results
    
    def get_changed_files(self) -> List[str]:
        """Get Python files changed between commits"""
        try:
            result = subprocess.run(
                ['git', 'diff', '--name-only', self.base_sha, self.head_sha],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode != 0:
                return []
            
            # Filter for Python files only
            files = [
                f for f in result.stdout.strip().split('\n') 
                if f.endswith('.py') and f and Path(self.repo_path / f).exists()
            ]
            
            return files
            
        except Exception as e:
            print(f"Warning: Could not get changed files: {e}", file=sys.stderr)
            return []
    
    def analyze_changes(self, changed_files: List[str]) -> Dict[str, List[str]]:
        """Analyze which functions changed in each file"""
        analysis = {}
        
        for file in changed_files:
            if file.startswith('test_'):
                # Skip test files in function analysis
                continue
                
            try:
                functions = self.get_changed_functions(file)
                if functions:
                    analysis[file] = functions
            except Exception as e:
                print(f"Warning: Could not analyze {file}: {e}", file=sys.stderr)
                continue
        
        return analysis
    
    def get_changed_functions(self, source_file: str) -> List[str]:
        """Get list of functions that changed in a file using AST"""
        full_path = self.repo_path / source_file
        
        if not full_path.exists():
            return []
        
        try:
            # Parse AST to get function line ranges
            with open(full_path, 'r', encoding='utf-8') as f:
                tree = ast.parse(f.read(), filename=str(full_path))
            
            functions = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Skip private functions and test functions
                    if not node.name.startswith('_') and not node.name.startswith('test_'):
                        functions[node.name] = (node.lineno, node.end_lineno)
            
            # Get changed lines from git diff
            changed_lines = self.get_changed_lines(source_file)
            
            if not changed_lines:
                return []
            
            # Match changed lines to functions
            changed_funcs = set()
            for func_name, (start, end) in functions.items():
                for line in changed_lines:
                    if start <= line <= end:
                        changed_funcs.add(func_name)
                        break
            
            return list(changed_funcs)
            
        except SyntaxError:
            # File might have syntax errors
            return []
        except Exception as e:
            print(f"Warning: AST analysis failed for {source_file}: {e}", file=sys.stderr)
            return []
    
    def get_changed_lines(self, file: str) -> Set[int]:
        """Get line numbers that changed in a file"""
        try:
            result = subprocess.run(
                ['git', 'diff', '-U0', self.base_sha, self.head_sha, '--', file],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=5
            )
            
            changed_lines = set()
            
            for line in result.stdout.split('\n'):
                if line.startswith('@@'):
                    # Parse hunk header: @@ -1,2 +3,4 @@
                    match = re.search(r'\+(\d+)(?:,(\d+))?', line)
                    if match:
                        start = int(match.group(1))
                        count = int(match.group(2)) if match.group(2) else 1
                        changed_lines.update(range(start, start + count))
            
            return changed_lines
            
        except Exception:
            return set()
    
    def select_tests(self, changed_files: List[str], analysis: Dict[str, List[str]]) -> List[str]:
        """Select which tests to run based on changes"""
        selected = set()
        
        # Safety checks - run all tests if:
        safety_files = [
            'setup.py', 'setup.cfg', 'pyproject.toml',
            'requirements.txt', 'Pipfile', 'tox.ini',
            'conftest.py', 'pytest.ini'
        ]
        
        for file in changed_files:
            file_name = Path(file).name
            if file_name in safety_files or file.startswith('.github/'):
                # Critical files changed - run everything
                return ['ALL']
        
        # Check for test files
        for file in changed_files:
            if file.startswith('test_') or '/test_' in file:
                selected.add(file)
        
        # For source files, find corresponding tests
        for file, functions in analysis.items():
            if not functions:
                # Changed but can't determine functions - be safe
                base_name = Path(file).stem
                test_file = f"test_{base_name}.py"
                
                # Check if test file exists
                if self._test_file_exists(test_file):
                    selected.add(test_file)
                continue
            
            # Build test patterns for changed functions
            for func in functions:
                pattern = f"test_{func}"
                selected.add(f"PATTERN:{pattern}")
        
        # If we found nothing, don't run any tests (no relevant changes)
        if not selected:
            return []
        
        return list(selected)
    
    def _test_file_exists(self, test_file: str) -> bool:
        """Check if a test file exists in common locations"""
        common_test_dirs = ['', 'tests/', 'test/']
        
        for dir in common_test_dirs:
            test_path = self.repo_path / dir / test_file
            if test_path.exists():
                return True
        
        return False
    
    def execute_tests(self) -> int:
        """Execute selected tests (for CLI mode)"""
        if not self.results['success']:
            print("Analysis failed, running all tests", file=sys.stderr)
            return self.run_all_tests()
        
        selected = self.results['selected_tests']
        
        if not selected:
            print("No tests need to run for these changes")
            return 0
        
        if selected == ['ALL']:
            print("Running full test suite (critical files changed)")
            return self.run_all_tests()
        
        # Build pytest command
        cmd = self.test_command.split()
        
        # Separate files from patterns
        files = [s for s in selected if not s.startswith('PATTERN:')]
        patterns = [s.replace('PATTERN:', '') for s in selected if s.startswith('PATTERN:')]
        
        if patterns:
            # Use -k for pattern matching
            pattern_str = ' or '.join(patterns)
            cmd.extend(['-k', pattern_str])
        
        if files:
            cmd.extend(files)
        
        print(f"Running: {' '.join(cmd)}")
        
        result = subprocess.run(cmd, cwd=self.repo_path)
        return result.returncode
    
    def run_all_tests(self) -> int:
        """Run full test suite"""
        result = subprocess.run(
            self.test_command.split(),
            cwd=self.repo_path
        )
        return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description='Smart CI - Intelligent test selection',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Run command
    run_parser = subparsers.add_parser('run', help='Analyze and run tests')
    run_parser.add_argument('--repo', default='.', help='Repository path')
    run_parser.add_argument('--base-sha', required=True, help='Base commit SHA')
    run_parser.add_argument('--head-sha', required=True, help='Head commit SHA')
    run_parser.add_argument('--test-command', default='pytest', help='Test command')
    run_parser.add_argument('--execute', action='store_true', help='Execute tests (default: just analyze)')
    
    # Analyze command (JSON output only)
    analyze_parser = subparsers.add_parser('analyze', help='Analyze only (JSON output)')
    analyze_parser.add_argument('--repo', default='.', help='Repository path')
    analyze_parser.add_argument('--base-sha', required=True, help='Base commit SHA')
    analyze_parser.add_argument('--head-sha', required=True, help='Head commit SHA')
    analyze_parser.add_argument('--test-command', default='pytest', help='Test command')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    ci = SmartCI(
        repo_path=args.repo,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        test_command=args.test_command
    )
    
    if args.command == 'analyze':
        # Just output JSON
        results = ci.run()
        print(json.dumps(results, indent=2))
        sys.exit(0)
    
    elif args.command == 'run':
        results = ci.run()
        
        if args.execute:
            # Run the tests
            exit_code = ci.execute_tests()
            sys.exit(exit_code)
        else:
            # Just show analysis
            print(json.dumps(results, indent=2))
            sys.exit(0)


if __name__ == '__main__':
    main()