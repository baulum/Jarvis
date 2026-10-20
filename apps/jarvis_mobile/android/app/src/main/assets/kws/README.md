Wake-word model for "Hey Jarvis" (sherpa-onnx keyword spotter, Apache-2.0).

Model: sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01
       https://github.com/k2-fsa/sherpa-onnx/releases/tag/kws-models
Files: encoder (int8), decoder, joiner (int8), tokens.txt – unchanged from that release.
keywords.txt: the phrase as BPE pieces of tokens.txt (`:` boost, `#` threshold). WakeDetector.kt passes the
same line with a threshold derived from the sensitivity setting; this file is only the default the engine loads.

Other phrase: tokenize it with the release's bpe.model (sentencepiece, upper case), e.g.
  python -c "import sentencepiece as s;print(' '.join(s.SentencePieceProcessor(model_file='bpe.model').encode('HEY JARVIS',out_type=str)))"
