import re


class GuardRails:
    # regex — первый, дешёвый фильтр. Он ловит только топорные атаки; хитрые останутся для LLM-классификатора
    INJECTION_RE = re.compile(
        r"ignore (all |the )?(previous|above|prior) (instructions|rules)"
        r"|disregard (your|the) (instructions|rules)"
        r"|(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)"
        r"|you are now|developer mode|jailbreak",
        re.IGNORECASE,
    )
    URL_RE = re.compile(r"https?://[^\s)\]>\"']+")
    # фразы, которые есть ТОЛЬКО в системном промпте: если они попали в ответ, промпт утёк
    PROMPT_MARKERS = (
        "ANSWER STYLE",
        "BOOKING A CALL",
        "Facts ONLY from the search_about_me",
    )
    LEAK_REPLY = (
        "I can't share my internal instructions. Ask me something about Andriy."
    )

    def is_injection(self, text: str) -> bool:
        return bool(self.INJECTION_RE.search(text))

    def leaks_prompt(self, answer: str) -> bool:
        low = answer.lower()
        return any(m.lower() in low for m in self.PROMPT_MARKERS)

    def check_answer(self, answer: str, allowed_text: str) -> tuple[str, list[str]]:
        if self.leaks_prompt(answer):
            return self.LEAK_REPLY, ["prompt_leak"]
        problems = []
        for url in set(self.URL_RE.findall(answer)):
            if url not in allowed_text:
                problems.append(f"invented_url: {url}")
                # cleaning the url, image
                answer = re.sub(
                    r"!?\[([^\]]*)\]\(" + re.escape(url) + r"\)", r"\1", answer
                )
                answer = answer.replace(url, "")
        return answer, problems
