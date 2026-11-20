# Riley Brown dataset

Riley Brown's public content (YouTube transcripts, X posts and newsletter posts), cleaned and ready for training. It's built to fine-tune a "Riley voice" model, for example a LoRA on `DeepSeek-R1-Distill-Llama-8B`.

Everything in this repo was already public. Nothing private went in: no emails, DMs, Slack, Notion, docs, finance or brand-deal data, and no third-party transcripts.

## At a glance

| Source | Items | Words |
|---|---:|---:|
| YouTube long-form (@rileybrownai, @rileybrown-2) | 188 | 1,012,963 |
| YouTube Shorts (@rileybrownai) | 47 | 16,527 |
| X posts (@rileybrown) | 205 | 15,390 |
| Newsletter (learn.agentnative.inc) | 20 | 4,499 |
| **Total** | **460** | **1,049,379** |

- Dates: January 2023 to November 2026
- `data/sft.jsonl`: 3,139 chat pairs for training
- `data/test.jsonl`: 50 held-out pairs. They come from 28 whole items, so no part of a test video or post appears in training.
- Exact numbers for each build are in `data/stats.json`.

## Layout

```
raw/youtube/<video_id>.txt / .json     cleaned transcript + metadata (source, url, date, title, channel, words)
raw/x/<post_id>.txt / .json            cleaned post + metadata
raw/newsletter/<slug>.txt / .json      cleaned post + metadata
data/corpus.jsonl                      {"text","source","url","date"[,"title","multi_speaker"]}, one item per line, for continued pretraining
data/sft.jsonl                         {"messages":[system,user,assistant],"source","url"}: the assistant turn is always Riley's real words
data/test.jsonl                        same format, held out
data/stats.json                        counts and word totals
scripts/                               pull + clean + build, re-runnable
```

### How the SFT pairs are made

The assistant turn is always unedited Riley text. Only the user turn is written from a template:

- **Long videos** are split at sentence boundaries into chunks of about 320 words.
  - Chunk 1 gets the prompt `Write the opening of your YouTube video titled "<title>".`
  - Later chunks get `Continue your YouTube video "<title>" (part k of n). This part covers <topics>. Pick up right after: "...<last 25 words>"`
- **Shorts** get `Write the script for a short-form vertical video titled "<title>".`
- **X posts** get `Write a tweet about <topics>.` Long posts are asked for as a long-form post, and his replies as `Write a short reply on X...`. Posts under 6 words (mostly media captions) stay in the corpus but are left out of SFT.
- **Newsletter** posts are split into sections of about 300 words and use the same opening/continue prompts.
- **Interviews** with a guest are left out of SFT because the transcripts have no speaker labels. They stay in `corpus.jsonl` with `"multi_speaker": true`. These are Bilawal Sidhu, Guillermo Rauch, David Shapiro, the Software Composer podcast, Buzz AI and the Ras Mic Codex episode.

## Cleaning

1. **Transcripts.** Collapsed caption line breaks and double spaces. Removed `[Music]`/`[laughter]`-style tags, `>>` speaker marks, the fillers "um"/"uh"/"erm", and doubled words ("the the"). Fixed capitalisation at sentence starts and regrouped the text into paragraphs. Old auto-captions with no punctuation are split into ~40-word pieces.
2. **Sponsor reads.** A sponsor read is dropped from its start cue ("this part of the video is sponsored by…", "the sponsor of today's video…") until a return cue or about 220 words. Single lines with "promo code", "link in the description" or "thanks to X for sponsoring" are also dropped. Shorts that are entirely sponsored (`#…Partner` in the title) are skipped. "This is not sponsored" lines are kept.
3. **Links.** Tracking and shortener links (`t.co`, `bit.ly`, and similar) are removed. `utm_*`, `ref`, `si`, `fbclid` and other tracking parameters are stripped from the remaining URLs. Leading @mentions on replies are removed. Retweets are excluded. Video descriptions are not included at all.
4. **Newsletter.** Removed chapter timestamps and Substack boilerplate ("Thanks for reading…", subscribe/share lines).
5. **Dedupe.** Items with the same normalised text are kept once.
6. **Privacy scrub.**
   - Emails, spoken emails ("x at y dot com"), phone numbers and key/token patterns (`sk-`, `sk-ant-`, `gh*_`, `github_pat_`, `AKIA…`, `AIza…`, `xox*-`, JWTs, Stripe `sk_/pk_`, `hf_`, `r8_`) are replaced with `[email]`, `[phone]` or `[redacted]`.
   - **The build fails if any match is left anywhere in `raw/` or `data/`.**
   - This build made 11 replacements, all email addresses spoken or shown on screen in videos.

## Sources and how they were pulled

- **YouTube.** `yt-dlp --flat-playlist` lists every upload on @rileybrownai (videos and Shorts) and @rileybrown-2. Publish dates come from the YouTube Data API. Transcripts come from Scrape Creators through the Composio CLI, with a local cache of about 20 transcripts reused. 236 of 237 uploads have a transcript. One Short has no speech.
- **X.** Scrape Creators through Composio. The ~100 most popular posts are pulled, then Google-indexed `from:rileybrown` searches across about 70 topics (11 pages each), filtered to posts he wrote. The official X API route returned "credits depleted" on build day, so this is the reachable public subset (205 posts after cleaning), not the full ~12k-post history. Top up X API credits and add a timeline pull to `scripts/pull_x.py` to go deeper.
- **Newsletter.** The public RSS feed of learn.agentnative.inc, keeping only posts bylined Riley Brown.
- **Not included.** Instagram (@realrileybrown). The Reels mostly duplicate the YouTube Shorts.

### Caveats

- Transcripts are automatic captions, so some product names are misheard: "Codeex", "GBT", and in one place "PaLM" for Fable.
- Some long-form videos have a co-host on screen. Their lines are not labelled.

## Rebuild

Needs `yt-dlp`, `curl`, Python 3.10+ and a logged-in Composio CLI with Scrape Creators and YouTube connected.

```bash
python3 scripts/pull_youtube.py      # -> work/yt/
python3 scripts/pull_x.py            # -> work/x/posts.json
python3 scripts/pull_newsletter.py   # -> work/newsletter/posts.json
python3 scripts/build.py             # -> raw/, data/  (exits 1 if the leak check fails)
```

`work/` holds the unprocessed pulls and is git-ignored.

## Train with Unsloth (LoRA on DeepSeek-R1-Distill-Llama-8B)

Tested shape for a single 24 GB+ GPU (4090, L40S, A100).

```python
from unsloth import FastLanguageModel
from unsloth.chat_templates import train_on_responses_only
from datasets import load_dataset
from trl import SFTTrainer, SFTConfig

model, tok = FastLanguageModel.from_pretrained(
    "unsloth/DeepSeek-R1-Distill-Llama-8B", max_seq_length=2048, load_in_4bit=True)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0,
    target_modules=["q_proj","k_proj","v_proj","o_proj","gate_proj","up_proj","down_proj"],
    use_gradient_checkpointing="unsloth")

ds = load_dataset("json", data_files={"train": "data/sft.jsonl", "test": "data/test.jsonl"})
fmt = lambda b: {"text": [tok.apply_chat_template(m, tokenize=False) for m in b["messages"]]}
ds = ds.map(fmt, batched=True, remove_columns=ds["train"].column_names)

trainer = SFTTrainer(
    model=model, tokenizer=tok, train_dataset=ds["train"], eval_dataset=ds["test"],
    args=SFTConfig(dataset_text_field="text", max_seq_length=2048, per_device_train_batch_size=4,
                   gradient_accumulation_steps=4, num_train_epochs=2, learning_rate=2e-4,
                   lr_scheduler_type="cosine", warmup_ratio=0.03, logging_steps=10,
                   eval_strategy="steps", eval_steps=100, output_dir="out", bf16=True))
trainer = train_on_responses_only(trainer,
    instruction_part="<｜User｜>", response_part="<｜Assistant｜>")   # learn Riley's turns only
trainer.train()
model.save_pretrained("riley-lora"); tok.save_pretrained("riley-lora")
```

Notes:

- R1-Distill is a reasoning model and its template expects a `<think>` block. Riley's turns have none, so the model learns to answer directly. If you want it to keep reasoning, prepend `<think>\n\n</think>\n\n` to each assistant turn before training.
- For style only, `corpus.jsonl` can be used first for one epoch of continued pretraining on raw text (about 1.05M words, roughly 1.4M tokens), followed by the SFT pass.
- 3,139 pairs × ~420 tokens ≈ 1.3M tokens per epoch. That is about 20–30 minutes per epoch on a 4090-class GPU.

## Licence

- **Scripts** (`scripts/`): MIT.
- **Content** (`raw/`, `data/`): © Riley Brown. Shared publicly for research and for training models of Riley's own voice. It may not be repackaged or resold as a dataset, or used to impersonate Riley.
- Third-party names and products mentioned in the content belong to their owners.
