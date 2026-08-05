# 06 — Ingestion roadmap

**Phase 1 accepts local files only. This document defines the interface now so the deferred work slots in without a refactor, and records the research that has to happen first.**

---

## The decision

> *"I've tried to build a system that can download social media videos with Claude Code before. I didn't put much effort in, but it didn't work great. I don't want to waste usage trying to make that work now. Let's focus on local, and when we want built-in URLs we can research how the other platforms that download from social sites actually work, then build our own — and if authentication is needed, a system where I sign in with my socials so it uses my tokens and session state."*

**Decided 4 August 2026: local files only for Phase 1–3.** URL ingestion is a separate module, built after research, not during the pipeline build.

The reasoning holds up independently of the earlier failed attempt. Downloading from social platforms is a **maintenance problem, not a build problem** — the code is not hard, it is that the platforms change it deliberately and often. Coupling that to the pipeline means every platform change breaks the whole tool. Kept at arm's length behind an interface, a platform change breaks one adapter.

---

## The interface, built in Phase 1

```python
class Source(Protocol):
    def resolve(self, spec: str) -> list[VideoRef]: ...

@dataclass
class VideoRef:
    path: Path
    origin: str | None          # URL if known, else None
    title: str | None
    description: str | None     # caption / post text — real signal, feed it to S6
    author: str | None
    published_at: datetime | None
    fetched_at: datetime | None
```

**Implemented in Phase 1:**

- `LocalFileSource` — a file path
- `LocalDirectorySource` — a folder, ordered by filename or mtime, which is how a saved series arrives
- `SidecarMetadataSource` — reads a `<video>.json` sitting beside the file, so metadata can be supplied by hand or by an external downloader

That third one is the whole trick. **It means a URL downloader is not needed to get the benefit of URL metadata.** Any external tool, or Mehdi pasting a caption into a text file, populates the same fields the eventual module would. The pipeline never knows the difference.

**Not implemented:** `UrlSource`. The protocol accommodates it; nothing satisfies it.

---

## Why the metadata matters more than the download

Easy to miss: **the caption is often the densest text in the entire artifact.** A build video's post text frequently contains the parts list, the dimensions, and the thing the narrator never says out loud. It is also the cheapest possible input — a few hundred tokens, no processing.

So `description` is passed into S6 as video-level context and into S7 as a synthesis input, and claims derived from it get a fourth modality value: **`stated`** — written by the author, distinct from `heard` in the audio.

Consequence: **capturing the caption by hand when saving a video is worth more than any amount of pipeline tuning.** Do that from day one, into the sidecar JSON.

---

## The research that has to happen before building `UrlSource`

Not now. Recorded so the eventual session starts from a question list rather than a blank page.

| # | Question |
|---|---|
| 1 | How do the existing public download services actually work — server-side extraction, headless browser, reverse-engineered internal endpoints, or the platform's own embed/oEmbed surfaces? Which approach survives longest? |
| 2 | Which platforms expose a legitimate route — official API, data-portability export, "download your own data"? Instagram and TikTok both have data-export mechanisms worth understanding before reaching for scraping. |
| 3 | Where does authentication become necessary rather than optional, and for which content types? |
| 4 | If session-based access is needed, what does the token actually permit, how long does it survive, and what triggers a platform to invalidate it or flag the account? |
| 5 | What do the terms of service say, per platform, for personal use versus a product? These differ materially and the difference is the whole commercial question. |
| 6 | What is the maintenance cadence — how often do the working approaches break in practice? That number decides whether this is worth owning at all. |

**Question 6 is the decisive one.** If the answer is "every few weeks", the correct decision is to never build it and instead keep the sidecar workflow, which never breaks.

---

## On the sign-in / session-token idea

Mehdi's proposal — sign in with his own accounts so downloads use his session — is technically the most robust approach, and it needs its risks stated plainly rather than discovered:

- **It is the account that pays.** Automated access using a personal session risks that account, not an anonymous IP. On a platform holding years of history and contacts, that is a real cost.
- **Session credentials are the most sensitive thing the tool would ever hold.** They must live in the OS keychain, never in the config file, never in the repo, never in a run directory, and never in a log line.
- **Rate discipline is the whole defence.** Human-plausible pacing, hard daily caps, no concurrency. The thing that gets accounts flagged is volume and rhythm, not the act itself.
- **It does not transfer to a product.** A tool that works by using the operator's own session cannot be sold, because the buyer's session becomes the buyer's risk and no reasonable terms of service permits it. This is the branch where personal tool and product genuinely diverge — worth knowing before building anything on top of it.

---

## The recommended workflow until then

1. Save the video by whatever means, into a corpus folder
2. Write a sidecar `<video>.json` with the URL, caption, author and date — thirty seconds, and it is the highest-value input per second of effort in the whole system
3. `stratum process <folder>`

Unglamorous, unbreakable, and it works today.
