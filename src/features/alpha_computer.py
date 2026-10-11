"""
Alpha computation from formulas
"""
import re
import numpy as np
import pandas as pd


class EnhancedAlphaComputer:
    """Improved alpha computation with caching and validation"""

    def __init__(self, df, clip_outliers=True):
        """
        Args:
            df: Feature frame.
            clip_outliers: Clip each alpha to mean +/- 3 std of the whole frame.
                The time-series prep passes False and clips with bounds fitted
                on the training rows only, so test rows don't shape the bounds.
        """
        self.df = df.copy()
        self.available_columns = set(df.columns)
        self.alpha_cache = {}
        self.clip_outliers = clip_outliers

    def parse_alpha_formula(self, formula_str):
        """Extract formula with better parsing"""
        # Remove alpha label and clean
        formula = re.sub(r'^[αA]lpha\s*\d+[\s:=→\'-]+', '', formula_str, flags=re.IGNORECASE)
        formula = formula.strip().strip('"').strip("'")

        # Replace special characters
        replacements = {
            '×': '*', '÷': '/', '−': '-', '–': '-',
            '^': '**', 'exp': 'np.exp', 'log': 'np.log',
            'sqrt': 'np.sqrt', 'abs': 'np.abs'
        }
        for old, new in replacements.items():
            formula = formula.replace(old, new)

        return formula

    def validate_formula(self, formula):
        """Validate formula syntax and columns"""
        try:
            # Check for invalid operations
            if re.search(r'/0([^.]|$)', formula) or '/ 0' in formula:
                print(f"    Warning: Potential division by zero: {formula[:50]}...")

            # Check column existence
            pattern = r'([A-Za-z][A-Za-z0-9_]*)(?=\s*[+\-*/()]|\s*$)'
            variables = re.findall(pattern, formula)

            missing_cols = []
            for var in variables:
                # Skip mathematical functions and constants
                if var in ['np', 'exp', 'log', 'sqrt', 'abs', 'e', 'pi']:
                    continue
                if var not in self.available_columns:
                    missing_cols.append(var)

            return len(missing_cols) == 0, missing_cols

        except Exception as e:
            return False, [f"Parse error: {str(e)}"]

    def compute_alpha(self, formula, alpha_name):
        """Compute alpha with caching and error handling"""
        # Check cache
        cache_key = f"{formula}_{len(self.df)}"
        if cache_key in self.alpha_cache:
            return self.alpha_cache[cache_key]

        try:
            is_valid, missing = self.validate_formula(formula)
            if not is_valid:
                print(f"    ✗ Invalid formula {alpha_name}: {missing}")
                return None

            print(f"    Computing {alpha_name}...")
            print(f"    Formula: {formula[:80]}...")

            # Extract variables used in formula
            pattern = r'\b([A-Za-z][A-Za-z0-9_]*)\b'
            variables_in_formula = set(re.findall(pattern, formula))
            variables_in_formula -= {'np', 'exp', 'log', 'sqrt', 'abs', 'e', 'pi'}

            missing_vars = [v for v in variables_in_formula if v not in self.available_columns]
            if missing_vars:
                print(f"    ⚠️ Missing columns: {missing_vars}")

            # Evaluate formula with access to DataFrame columns
            namespace = {'np': np}
            namespace.update(self.df.to_dict('series'))

            result = eval(formula, {"__builtins__": {}}, namespace)

            if result is None or (hasattr(result, 'size') and result.size == 0):
                return None

            # Convert to Series
            if isinstance(result, (int, float)):
                result = pd.Series([result] * len(self.df), index=self.df.index)
            elif isinstance(result, pd.DataFrame):
                if result.shape[1] > 0:
                    result = result.iloc[:, 0]
                else:
                    return None
            elif not isinstance(result, pd.Series):
                result = pd.Series(result, index=self.df.index)

            # Check for invalid values
            if result.isna().all() or result.isnull().all():
                return None

            result = result.astype(float).replace([np.inf, -np.inf], np.nan)
            if result.isna().all():
                return None

            # Remove extreme outliers (cap at 3 standard deviations)
            if self.clip_outliers:
                mean = result.mean()
                std = result.std()
                if std > 0:
                    result = result.clip(mean - 3*std, mean + 3*std)

            # Cache result
            self.alpha_cache[cache_key] = result

            return result

        except Exception as e:
            print(f"    Error computing {alpha_name}: {str(e)[:100]}")
            import traceback
            traceback.print_exc()
            return None

    def add_alphas_from_text(self, alpha_text, max_alphas=5):
        """Parse and compute alphas from LLM text"""
        print(f"\n  DEBUG: Received alpha_text:")
        print(f"  Length: {len(alpha_text)} characters")
        print(f"  First 200 chars: {alpha_text[:200]}")
        print(f"  ---")

        lines = alpha_text.split('\n')
        print(f"  DEBUG: Split into {len(lines)} lines")

        alphas_added = 0
        attempted = 0

        print(f"  Available columns: {len(self.available_columns)} total")

        for line in lines:
            if alphas_added >= max_alphas:
                break

            if '=' in line and any(c.isalpha() for c in line):
                attempted += 1
                print(f"  DEBUG: Attempting line {attempted}: {line[:80]}")

                # Extract alpha name and formula
                match = re.match(r'([αA](?:lpha)?\s*\d+)\s*[:=\-→]+\s*(.+)', line.strip())
                if match:
                    alpha_name, formula_str = match.groups()
                    print(f"    Matched: {alpha_name} -> {formula_str[:60]}")
                    formula = self.parse_alpha_formula(formula_str)

                    if not formula or len(formula) < 3:
                        print(f"    ✗ Formula too short or empty: '{formula}'")
                        continue

                    # Compute alpha
                    alpha_values = self.compute_alpha(formula, alpha_name)

                    if alpha_values is not None:
                        alphas_added += 1
                        col_name = f'alpha_{alphas_added}'
                        self.df[col_name] = alpha_values

                        # Calculate correlation with target for debugging
                        if 'target' in self.df.columns:
                            corr = self.df[col_name].corr(self.df['target'])
                            print(f"  ✓ {col_name}: corr={corr:.3f}, {formula[:60]}...")
                        else:
                            print(f"  ✓ {col_name}: {formula[:60]}...")
                    else:
                        print(f"  Could not compute: {line[:80]}...")
                else:
                    print(f"    Regex didn't match: {line[:80]}")

        print(f"  Summary: {alphas_added}/{attempted} alphas computed successfully")

        if alphas_added == 0:
            print(f"  No alphas computed! Using simple technical indicators...")
            self.df = self.add_fallback_alphas()
            alphas_added = len([c for c in self.df.columns if c.startswith('alpha_')])

        return self.df, alphas_added

    def add_fallback_alphas(self):
        """Add fallback technical alphas when LLM fails.

        Columns are numbered contiguously (alpha_1..alpha_k) so the count always
        matches what the model receives, even when RSI/MACD are unavailable.
        """
        print("  Adding fallback technical alphas...")

        candidates = []
        if 'close' in self.df.columns:
            close = self.df['close']
            candidates.append(close.pct_change(5))                     # 5-day return
            candidates.append(close.pct_change(10))                    # 10-day return
            candidates.append(close.rolling(20).mean() / close - 1)    # SMA ratio

        if 'RSI' in self.df.columns:
            candidates.append((self.df['RSI'] - 50) / 50)              # Normalized RSI

        if 'MACD' in self.df.columns and 'MACD_Signal' in self.df.columns:
            candidates.append(self.df['MACD'] - self.df['MACD_Signal'])  # MACD histogram

        for i, values in enumerate(candidates, start=1):
            self.df[f'alpha_{i}'] = values

        return self.df