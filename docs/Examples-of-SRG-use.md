# Examples of SRG model use

Comparing the default model to other models  

All examples use the DEMO engagement

## Default model, Gemma4:E4B-it-qat, on SI-5

`srg generate SI-5 --context "CISA alerts are received by the State SOC and forwarded to all system owners via internal controlled channels."`

![](images/image1.png)

The model produced a viable draft, and included the input from the user along with the demo system context and tied those to the NIST control requirements.

## Same prompt but with a slightly less capable model

`SRG_GEN_MODEL=llama3.1:8b srg generate SI-5 --context "CISA alerts are received by the State SOC and forwarded to all system owners via internal controlled channels."`

![](images/image2.png)

Note that the model still produced a viable draft, but didn't quite understand how to incorporate the information provided by `--context`. This sort of omission or misunderstanding can be subtle sometimes, and requires the attention of the user. The response is still a pretty good draft, but requires more review and editing than the default model.

## Same prompt but with a similarly capable model that won't stay on topic

`SRG_GEN_MODEL=granite4.1:8b srg generate SI-5 --context "CISA alerts are received by the State SOC and forwarded to all system owners via internal controlled channels."`

![](images/image3.png)

The granite4.1:8B model from IBM is very accurate and thorough, literally tailor made for enterprise RAG scenarios, but tends to drift from the requested control as the highlighted areas show.  It occasionally will entirely shift the output style [see below].  This model also uses a bit more VRAM than an 8GB card can accomodate concurrently with the embeddingemma model.

![](images/image4.png)

Another example of granite4.1:8B, showing a wildly different output style and still drifting a little bit.  It flips between this style and prose unexpectedly, even if asked the same question.

## Smaller models are too weak and inconsistent

`SRG_GEN_MODEL=phi4-mini srg generate "SC-8(1)" --context "The system uses TLS 1.3 with post quantum safe algorithms to protect the confidentiality and integrity of information during transmission."`

![](images/image5.png)

This Phi4-mini model often output no response.  In testing, another very small model, Llama3.2:3B, was better at consistently generating *some sort of* output, but that output was often wrong, making false or contradictory claims.  The grantite4.1:**3b** model while generating different style output from Llama3.2:3B, still didn't generate anything close to a viable draft.

## Conclusion

Larger models are generally more reliable. The default Gemma4:E4B-it-qat model fits comfortably alongside the enbeddinggemma model in 8GB of VRAM and will reliably produce a viable draft.  Smaller models like Phi4-mini and Llama3.2:3B are currently inappropriate due to their variablity of response quality and alignment.  Several other models were tested and dismissed for various reasons.  All models tested are listed in the table below.

---
### Table of tested models
| **Model** | **Observations** |
| --- | --- |
| Phi4-mini (3.8B) | Inconsistent, sometimes no output |
| Llama3.2:3B | Poor alignment, sometimes wrong |
| granite4.1:3B | Fair alignment, harsh prose, VERY temperature sensitive |
| Qwen3.5:4B | Surprisingly good quality for a model this size, slow to output due to thinking, best sub 8B model by far, Chinese origin makes it unlikely to get customer approval for use. |
| Gemma3:4B | OK alignment but missed some details |
| Gemma4:E2B | OK alignment but still missed some details even though a newer generation |
| Llama3.1:8B | Consistent, generally well aligned, a mostly viable alternative to the default model due to quality, consistency, and 7GB VRAM usage size |
| granite4.1:8B | Thorough, sometimes harsh prose, needs 8GB of VRAM thus won't fit on an 8GB card alongside embeddinggemma and other system activity |
| Qwen3:8B | Good alignment, 5.2GB VRAM usage would fit an 8GB card, Chinese origin makes it unlikely to get customer approval for use. |
| Gemma4:E4B | Very good alignment and prose, but uses too much VRAM for even a 12GB card |
| Gemma4:E4B-it-qat | Essentially Gemma4:E4B with a smaller VRAM footprint.  Set as the default model. |

None of the tested 3B and 4B U.S.-based models produced output strong enough to recommend their use.  The Qwen3.5:4B model did surprisingly well for the size but was slow.  Passing it some parameters to disable thinking and vision might have increased the speed.
Of the U.S.-based models, 8B appears to be the minimum for reliably aligned and consistent output that would actually save the user time compared to hand crafting responses.  

Note the Gemma4:E4B is effectively 8B in this use case, but uses even more VRAM than either of the dedicated 8B models tested and would not fit comfortably on a 12GB card, where only 10GB of that was available to Ollama due to other system activity. The Gemma4:E4B`-it-qat` variant achieves nearly the same quality with under 4GB of VRAM.
