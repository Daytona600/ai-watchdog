from pathlib import Path

def add_attention(attention_file, message):
    with open(attention_file, "a") as f:
        f.write(f"- {message}\n")
