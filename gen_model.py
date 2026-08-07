#!/usr/bin/env python3
"""Generate a random-initialized tiny llama-arch GGUF with a byte-level BPE vocab.

Vocab: 256 byte tokens (GPT-2 byte-level alphabet) + <|endoftext|> = 257.
With no merges, every pre-token splits into single bytes: 1 token == 1 byte,
so NLL/ln(2) is bits-per-byte directly, comparable across languages with no
tokenizer confound. Uses tokenizer_pre="kimi-k2"; the patched llama.cpp b6000
loader tolerates the absent merges array.

Usage: [M_E=256 M_L=4 M_H=4 M_F=768] python3 gen_model.py out.gguf [seed]
Defaults produce the 11.7M-param variant; the env above produces 3.5M.
"""
import os, sys
import numpy as np

BASE = os.environ.get("PHYSLEAN_BASE", os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "llama.cpp", "gguf-py"))
import gguf

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "models", "base_11m.gguf")
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 1234

E   = int(os.environ.get("M_E", 384))
L   = int(os.environ.get("M_L", 6))
H   = int(os.environ.get("M_H", 6))
KV  = H
F   = int(os.environ.get("M_F", 1152))
CTX = int(os.environ.get("M_CTX", 2048))
V   = 257
rng = np.random.default_rng(SEED)

def bytes_to_unicode():
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("\xa1"), ord("\xac") + 1)) + list(range(ord("\xae"), ord("\xff") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b); cs.append(256 + n); n += 1
    return dict(zip(bs, [chr(c) for c in cs]))

b2u = bytes_to_unicode()
tokens = [b2u[b] for b in range(256)] + ["<|endoftext|>"]
types = [int(gguf.TokenType.NORMAL)] * 256 + [int(gguf.TokenType.CONTROL)]

w = gguf.GGUFWriter(OUT, "llama")
w.add_name(f"bytelm-e{E}l{L}")
w.add_file_type(gguf.LlamaFileType.ALL_F32)
w.add_context_length(CTX)
w.add_embedding_length(E)
w.add_block_count(L)
w.add_feed_forward_length(F)
w.add_head_count(H)
w.add_head_count_kv(KV)
w.add_rope_dimension_count(E // H)
w.add_rope_freq_base(10000.0)
w.add_layer_norm_rms_eps(1e-5)
w.add_vocab_size(V)
w.add_tokenizer_model("gpt2")
w.add_tokenizer_pre("kimi-k2")
w.add_token_list(tokens)
w.add_token_types(types)
w.add_token_merges([])
w.add_bos_token_id(256)
w.add_eos_token_id(256)
w.add_add_bos_token(False)
w.add_add_eos_token(False)

def norm(*shape, std=0.02):
    return (rng.standard_normal(shape) * std).astype(np.float32)

w.add_tensor("token_embd.weight", norm(V, E))
w.add_tensor("output_norm.weight", np.ones(E, dtype=np.float32))
w.add_tensor("output.weight", norm(V, E))
res_std = 0.02 / np.sqrt(2 * L)
for i in range(L):
    p = f"blk.{i}."
    w.add_tensor(p + "attn_norm.weight", np.ones(E, dtype=np.float32))
    w.add_tensor(p + "attn_q.weight", norm(E, E))
    w.add_tensor(p + "attn_k.weight", norm(E, E))
    w.add_tensor(p + "attn_v.weight", norm(E, E))
    w.add_tensor(p + "attn_output.weight", norm(E, E, std=res_std))
    w.add_tensor(p + "ffn_norm.weight", np.ones(E, dtype=np.float32))
    w.add_tensor(p + "ffn_gate.weight", norm(F, E))
    w.add_tensor(p + "ffn_up.weight", norm(F, E))
    w.add_tensor(p + "ffn_down.weight", norm(E, F, std=res_std))

w.write_header_to_file()
w.write_kv_data_to_file()
w.write_tensors_to_file()
w.close()
n_params = V*E*2 + E + L*(2*E + 4*E*E + 3*E*F)
print(f"wrote {OUT}  (~{n_params/1e6:.1f}M params)")
