# IoT2025 Conference - Predictability of DT through Active Inference

This project demonstrates the ability of an AIF-enabled DT to predict and optimise the performance of IoT processing services.


## Installation

### Setup basic requirements

Create a new virtual environment and install dependencies. It was developed and tested with Python3.12

```bash
python3 -m venv venv
source venv/bin/activate
python3 -m pip install -r ./requirements.txt
```

## Start Experiments

### Start processing environment 

with Docker installed, start all processing services

```bash
docker compose up -d
```

### Start AIF agent

start the AIF agent 

```bash
PYTHONPATH=. python3 DT_elastic-workbench/IoT2025/predictability_DT_AIF_agent.py
```





