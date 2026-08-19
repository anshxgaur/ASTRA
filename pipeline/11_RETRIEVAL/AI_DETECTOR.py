"""
11_RETRIEVAL -- AI_DETECTOR.py
Detect whether content is AI-generated or human-written using Groq LLM.

Features:
  - Multi-signal analysis (burstiness, repetition, vocabulary)
  - Groq-powered deep analysis
  - Batch detection for multiple texts
  - Detailed scoring with explanations
  - Confidence levels (HIGH / MEDIUM / LOW)
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import statistics
from collections import Counter
from pathlib import Path

PIPELINE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PIPELINE_ROOT.parent

sys.path.insert(0, str(PIPELINE_ROOT / "11_RETRIEVAL"))

import GROQ_CLIENT


class StatisticalAnalyzer:

    @staticmethod
    def word_entropy(text: str) -> float:
        words = text.lower().split()
        if not words:
            return 0.0
        freq = Counter(words)
        total = len(words)
        entropy = -sum((c / total) * math.log2(c / total) for c in freq.values())
        return entropy

    @staticmethod
    def burstiness(text: str) -> float:
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
        if len(sentences) < 2:
            return 0.0
        lengths = [len(s.split()) for s in sentences]
        if len(lengths) < 2:
            return 0.0
        mean_len = statistics.mean(lengths)
        if mean_len == 0:
            return 0.0
        std_len = statistics.stdev(lengths) if len(lengths) > 1 else 0
        return std_len / mean_len

    @staticmethod
    def vocabulary_richness(text: str) -> float:
        words = text.lower().split()
        if not words:
            return 0.0
        return len(set(words)) / len(words)

    @staticmethod
    def repetition_score(text: str) -> float:
        words = text.lower().split()
        if len(words) < 3:
            return 0.0
        bigrams = [f"{words[i]} {words[i+1]}" for i in range(len(words) - 1)]
        bigram_freq = Counter(bigrams)
        bigram_repeat = sum(1 for c in bigram_freq.values() if c > 2) / max(len(bigrams), 1)
        trigrams = [f"{words[i]} {words[i+1]} {words[i+2]}" for i in range(len(words) - 2)]
        trigram_freq = Counter(trigrams)
        trigram_repeat = sum(1 for c in trigram_freq.values() if c > 2) / max(len(trigrams), 1)
        return (bigram_repeat + trigram_repeat) / 2

    @staticmethod
    def transition_density(text: str) -> float:
        transitions = [
            "furthermore", "moreover", "additionally", "consequently",
            "however", "nevertheless", "nonetheless", "therefore",
            "thus", "hence", "accordingly", "subsequently",
            "in addition", "on the other hand", "in contrast",
            "it is important to note", "it is worth noting",
            "in conclusion", "to summarize", "overall",
        ]
        text_lower = text.lower()
        count = sum(1 for t in transitions if t in text_lower)
        word_count = len(text.split())
        if word_count == 0:
            return 0.0
        return count / word_count * 1000

    @staticmethod
    def sentence_length_variance(text: str) -> float:
        sentences = re.split(r"[.!?]+", text)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 5]
        if len(sentences) < 3:
            return 0.0
        lengths = [len(s.split()) for s in sentences]
        return statistics.variance(lengths) if len(lengths) > 1 else 0.0

    @staticmethod
    def personal_pronoun_density(text: str) -> float:
        personal = ["i", "me", "my", "mine", "we", "us", "our", "you", "your"]
        words = text.lower().split()
        if not words:
            return 0.0
        count = sum(1 for w in words if w in personal)
        return count / len(words) * 1000

    @staticmethod
    def hedge_word_density(text: str) -> float:
        hedges = [
            "might", "could", "possibly", "perhaps", "may",
            "potentially", "seemingly", "arguably", "apparently",
            "it appears", "it seems", "in some cases", "to some extent",
        ]
        text_lower = text.lower()
        count = sum(1 for h in hedges if h in text_lower)
        word_count = len(text.split())
        if word_count == 0:
            return 0.0
        return count / word_count * 1000

    @staticmethod
    def analyze(text: str) -> dict:
        return {
            "word_entropy": round(StatisticalAnalyzer.word_entropy(text), 4),
            "burstiness": round(StatisticalAnalyzer.burstiness(text), 4),
            "vocabulary_richness": round(StatisticalAnalyzer.vocabulary_richness(text), 4),
            "repetition_score": round(StatisticalAnalyzer.repetition_score(text), 4),
            "transition_density": round(StatisticalAnalyzer.transition_density(text), 4),
            "sentence_length_variance": round(StatisticalAnalyzer.sentence_length_variance(text), 4),
            "personal_pronoun_density": round(StatisticalAnalyzer.personal_pronoun_density(text), 4),
            "hedge_word_density": round(StatisticalAnalyzer.hedge_word_density(text), 4),
        }


def llm_detect(text: str, max_chars: int = 4000) -> dict:
    truncated = text[:max_chars]
    if len(text) > max_chars:
        truncated += "\n\n[... truncated, showing first {} of {} characters]".format(max_chars, len(text))

    prompt = (
        "You are an AI text detection expert. Analyze the following text and "
        "determine whether it was written by a human or generated by an AI "
        "(like ChatGPT, Claude, etc.).\n\n"
        "Look for these indicators of AI-generated text:\n"
        "1. Overly uniform sentence structure and length\n"
        "2. Excessive use of transition words (furthermore, moreover, additionally)\n"
        "3. Generic, non-specific statements lacking personal voice\n"
        "4. Perfect grammar with no natural errors or informalities\n"
        "5. Balanced, diplomatic tone avoiding strong opinions\n"
        "6. Repetitive phrasing patterns\n"
        "7. Lack of personal anecdotes or specific examples\n"
        "8. Overly structured formatting (bullet points, numbered lists)\n"
        "9. Hedging language (it is worth noting, it is important to mention)\n"
        "10. Lack of domain-specific jargon or colloquialisms\n\n"
        "TEXT TO ANALYZE:\n---\n" + truncated + "\n---\n\n"
        "Respond in this EXACT JSON format (no other text):\n"
        '{"is_ai_generated": true or false, '
        '"confidence": "HIGH" or "MEDIUM" or "LOW", '
        '"score": 0.0 to 1.0, '
        '"reasoning": "Brief explanation of your analysis", '
        '"signals": ["signal1", "signal2", "signal3"]}'
    )

    reply = GROQ_CLIENT.chat(
        [{"role": "user", "content": prompt}],
        temperature=0.1,
        max_tokens=500,
    )

    if reply is None:
        return {
            "is_ai_generated": None,
            "confidence": "NONE",
            "score": None,
            "reasoning": "Groq unavailable -- using statistical analysis only",
            "signals": [],
            "llm": "unavailable",
        }

    try:
        json_match = re.search(r"\{[^{}]*\}", reply, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            result["llm"] = "groq"
            return result
        else:
            raise ValueError("No JSON found in response")
    except (json.JSONDecodeError, ValueError):
        return {
            "is_ai_generated": None,
            "confidence": "LOW",
            "score": None,
            "reasoning": reply[:500],
            "signals": [],
            "llm": "groq_parse_error",
        }


class AIDetector:

    def __init__(self):
        self.stat_analyzer = StatisticalAnalyzer()

    def _compute_statistical_score(self, stats: dict) -> float:
        score = 0.5
        if stats["burstiness"] < 0.3:
            score += 0.15
        elif stats["burstiness"] > 0.6:
            score -= 0.15
        if stats["vocabulary_richness"] < 0.6:
            score += 0.1
        elif stats["vocabulary_richness"] > 0.8:
            score -= 0.1
        if stats["repetition_score"] > 0.1:
            score += 0.1
        elif stats["repetition_score"] < 0.02:
            score -= 0.05
        if stats["transition_density"] > 15:
            score += 0.1
        elif stats["transition_density"] < 5:
            score -= 0.05
        if stats["sentence_length_variance"] < 100:
            score += 0.1
        elif stats["sentence_length_variance"] > 400:
            score -= 0.1
        if stats["personal_pronoun_density"] < 10:
            score += 0.05
        elif stats["personal_pronoun_density"] > 30:
            score -= 0.1
        if stats["hedge_word_density"] > 8:
            score += 0.05
        elif stats["hedge_word_density"] < 2:
            score -= 0.05
        return max(0.0, min(1.0, score))

    def _score_to_label(self, score: float) -> tuple:
        if score >= 0.7:
            return True, "HIGH"
        elif score >= 0.55:
            return True, "MEDIUM"
        elif score >= 0.45:
            return None, "LOW"
        elif score >= 0.3:
            return False, "MEDIUM"
        else:
            return False, "HIGH"

    def analyze(self, text: str, use_llm: bool = True) -> dict:
        features = self.stat_analyzer.analyze(text)
        stat_score = self._compute_statistical_score(features)

        llm_result = None
        llm_score = None

        if use_llm and GROQ_CLIENT.is_available():
            llm_result = llm_detect(text)
            if llm_result.get("score") is not None:
                llm_score = llm_result["score"]

        if llm_score is not None:
            final_score = (stat_score * 0.4) + (llm_score * 0.6)
        else:
            final_score = stat_score

        is_ai, confidence = self._score_to_label(final_score)

        explanation_parts = []
        if is_ai is True:
            explanation_parts.append("This text LIKELY was AI-generated.")
        elif is_ai is False:
            explanation_parts.append("This text LIKELY was human-written.")
        else:
            explanation_parts.append("Cannot determine with confidence.")

        signals = []
        if features["burstiness"] < 0.3:
            signals.append("Low sentence length variation (uniform structure)")
        if features["vocabulary_richness"] < 0.6:
            signals.append("Limited vocabulary diversity")
        if features["transition_density"] > 15:
            signals.append("High use of transition words")
        if features["personal_pronoun_density"] < 10:
            signals.append("Few personal pronouns (detached tone)")
        if features["repetition_score"] > 0.1:
            signals.append("Repetitive phrasing patterns")
        if features["hedge_word_density"] > 8:
            signals.append("Heavy hedging/uncertainty language")

        explanation_parts.append(
            "Key signals: " + ("; ".join(signals) if signals else "None detected")
        )

        return {
            "is_ai_generated": is_ai,
            "confidence": confidence,
            "statistical_score": round(stat_score, 4),
            "llm_score": llm_score,
            "final_score": round(final_score, 4),
            "features": features,
            "llm_analysis": llm_result,
            "explanation": " ".join(explanation_parts),
        }

    def analyze_batch(self, texts: list, use_llm: bool = True) -> dict:
        results = []
        for i, text in enumerate(texts):
            result = self.analyze(text, use_llm=use_llm)
            result["index"] = i
            result["text_preview"] = text[:100] + "..." if len(text) > 100 else text
            results.append(result)

        ai_count = sum(1 for r in results if r["is_ai_generated"] is True)
        human_count = sum(1 for r in results if r["is_ai_generated"] is False)
        uncertain_count = sum(1 for r in results if r["is_ai_generated"] is None)
        avg_score = statistics.mean([r["final_score"] for r in results])

        return {
            "total_texts": len(texts),
            "ai_detected": ai_count,
            "human_detected": human_count,
            "uncertain": uncertain_count,
            "average_ai_score": round(avg_score, 4),
            "results": results,
        }

    def compare_texts(self, text_a: str, text_b: str) -> dict:
        result_a = self.analyze(text_a)
        result_b = self.analyze(text_b)

        return {
            "text_a": {
                "preview": text_a[:100] + "...",
                "ai_score": result_a["final_score"],
                "is_ai": result_a["is_ai_generated"],
            },
            "text_b": {
                "preview": text_b[:100] + "...",
                "ai_score": result_b["final_score"],
                "is_ai": result_b["is_ai_generated"],
            },
            "more_ai_like": "A" if result_a["final_score"] > result_b["final_score"] else "B",
            "score_difference": round(abs(result_a["final_score"] - result_b["final_score"]), 4),
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AI Text Detection")
    sub = parser.add_subparsers(dest="command")

    p_analyze = sub.add_parser("analyze", help="Analyze text for AI detection")
    p_analyze.add_argument("text", nargs="?", help="Text to analyze")
    p_analyze.add_argument("--file", help="File containing text")
    p_analyze.add_argument("--no-llm", action="store_true")

    args = parser.parse_args()
    detector = AIDetector()

    if args.command == "analyze":
        text = args.text
        if args.file:
            text = Path(args.file).read_text(encoding="utf-8")
        elif not text:
            print("Provide text or --file")
            sys.exit(1)

        result = detector.analyze(text, use_llm=not args.no_llm)

        print("\n" + "=" * 60)
        print("AI DETECTION RESULTS")
        print("=" * 60)
        print("  AI Generated:  {}".format(result["is_ai_generated"]))
        print("  Confidence:    {}".format(result["confidence"]))
        print("  Final Score:   {:.2%}".format(result["final_score"]))
        print("  Statistical:   {:.2%}".format(result["statistical_score"]))
        if result.get("llm_score") is not None:
            print("  LLM Score:     {:.2%}".format(result["llm_score"]))
        print("\n  Explanation: {}".format(result["explanation"]))
        print("\n  Features:")
        for k, v in result["features"].items():
            print("    {}: {}".format(k, v))
        print("=" * 60)
    else:
        parser.print_help()
