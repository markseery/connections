from mlx_lm import load, generate
from huggingface_hub import login
login(token=os.getenv("HF_TOKEN"))

modelname = "mlx-community/Mistral-7B-Instruct-v0.3-4bit"

model, tokenizer = load(modelname)
prompt = "Write a story about Einstein"

messages = [{"role": "user", "content": prompt}]
prompt = tokenizer.apply_chat_template(
    messages, add_generation_prompt=True,
)

text = generate(model, tokenizer, prompt=prompt, verbose=True)