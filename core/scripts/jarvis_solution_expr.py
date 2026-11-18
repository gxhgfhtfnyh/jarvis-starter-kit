# -*- coding: utf-8 -*-
"""My own solution to the EXPR_TASK (arithmetic expression evaluator), written directly by me
(Sonnet 5.5, this session), no model call -- the real head-to-head baseline he asked for."""
import re


def evaluate(expr):
    tokens = re.findall(r"\d+\.?\d*|[+\-*/()]", expr.replace(" ", ""))
    pos = [0]

    def peek():
        return tokens[pos[0]] if pos[0] < len(tokens) else None

    def advance():
        tok = tokens[pos[0]]
        pos[0] += 1
        return tok

    def parse_expr():
        value = parse_term()
        while peek() in ("+", "-"):
            op = advance()
            rhs = parse_term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def parse_term():
        value = parse_factor()
        while peek() in ("*", "/"):
            op = advance()
            rhs = parse_factor()
            if op == "*":
                value = value * rhs
            else:
                if rhs == 0:
                    raise ZeroDivisionError("division by zero")
                value = value / rhs
        return value

    def parse_factor():
        if peek() == "-":
            advance()
            return -parse_factor()
        if peek() == "(":
            advance()
            value = parse_expr()
            advance()  # consume ")"
            return value
        return float(advance())

    return parse_expr()
