# Supplier Message Templates (Chinese) — confirm-then-send

> **Confirm-then-send: the assistant sends via Wangwang (旺旺) ONLY after the human confirms
> each message. No blind auto-send — per-message approval, human-paced.**
> Claude fills the `{{variables}}`, writes the message in natural, polite business
> Chinese, shows it with a short English gloss, and sends it on the human's OK.
> Keep messages concise — suppliers reply faster to short, specific asks.

> **⚠️ SHIPPING RULE (verified, remember this): Taobao sellers ship DOMESTICALLY within
> mainland China only — they do NOT ship internationally and don't need to.** The buyer
> handles the international leg via a China forwarder / 集运 / agent (seller ships to a
> domestic forwarder address). So **never ask a seller about international
> shipping, freight cost, or export experience** — it just confuses them. Focus seller
> messages on: price + bulk, condition/testing, accessories, MOQ, packaging, and invoice.

## Variables
`{{product}}` item name/ID · `{{variant}}` the exact SKU (e.g. 黑色 / L) ·
`{{qty}}` quantity · `{{country}}` destination (the operator's country) ·
`{{specs}}` requested specs · `{{logo}}` brand/logo.

---

## 1. First contact — MOQ + price tiers
你好，我对【{{product}}】很感兴趣，想做{{country}}市场的批发。请问：
1）起订量（MOQ）是多少？
2）不同数量的批发价格分别是多少（例如 10件 / 50件 / 100件）？
3）{{variant}} 这个款式现货充足吗？
谢谢！

*EN: Intro + interest for {{country}} resale; asks MOQ, tiered wholesale prices (10/50/100), and stock for {{variant}}.*

## 2. Unit price at a specific quantity
你好，如果我一次订购 {{qty}} 件【{{product}}】（{{variant}}），单价能做到多少？可以开发票吗？

*EN: Best unit price for {{qty}} units of {{variant}}, and whether an invoice (发票) can be issued.*

## 3. Packaging (buyer re-ships internationally — ask about protection, NOT shipping)
你好，{{product}} 发货时麻烦加固包装，做好防静电和防震，我收到后还要转运，怕路上磕碰损坏。可以吗？

*EN: Asks for reinforced, anti-static, shock-proof packaging because the buyer re-ships
onward. Do NOT ask the seller about international shipping — they ship
domestically only; the buyer's forwarder address is given at checkout.*

## 4. Lead time
你好，{{qty}} 件【{{product}}】现货能马上发吗？如果需要备货，大概几天能发货？

*EN: Is {{qty}} in stock to ship now, or what's the restock/lead time?*

## 5. Customization / OEM
你好，请问【{{product}}】可以定制吗？
1）能否按 {{specs}} 调整？
2）能否贴我们的品牌/logo（{{logo}}）？
3）定制的起订量和打样费用是多少？

*EN: Customization to {{specs}}, private-label with {{logo}}, plus custom MOQ and sampling cost.*

## 6. Sample request
你好，下大货订单前我想先订 1–2 件 {{variant}} 做样品确认质量，可以吗？样品价格和运费怎么算？

*EN: Order 1–2 samples of {{variant}} to verify quality before bulk; asks sample price + shipping.*

## 7. Quality / warranty / after-sales
你好，请问【{{product}}】的质保是多久？如果到货有质量问题或损坏，怎么处理？批量订单的次品率大概多少？

*EN: Warranty length, handling of defects/damage on arrival, and typical defect rate on bulk orders.*

---

### Drafting notes for Claude
- Combine asks (1+3+4) into one message when first contacting — fewer round-trips.
- Mirror the human's intent; don't invent commitments (quantities, prices, dates)
  the human hasn't stated.
- After drafting, offer to send it via `taobao_message(action="reply")` — preview with
  `confirm=False`, then send **only after the human OKs that exact message**
  (`confirm=True`). Never send a message the human hasn't approved verbatim.
