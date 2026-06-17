# GPU VRAM High

A GPU is using a high amount of VRAM.

On this system, RTX 4070 SUPER VRAM can be high because of Parakeet STT, Ollama, Frigate, and ComfyUI.

Check GPU users:

nvidia-smi
nvidia-smi pmon -c 1

Show process commands:

for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | sort -u); do
  echo
  echo "PID $pid"
  ps -fp "$pid"
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null
  echo
done
