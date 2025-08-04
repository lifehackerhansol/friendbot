FROM python:3.11-bookworm

ENV HOME /home/friendbot
RUN useradd -m friendbot
WORKDIR $HOME
COPY ./requirements.txt .
RUN python3 -m pip install -r requirements.txt
USER friendbot

COPY const.py const.py
COPY friend_functions.py friend_functions.py
COPY seedbot.py seedbot.py
COPY webhandler.py webhandler.py

RUN ln -sf /run/secrets/clcerta ClCertA.pem
RUN ln -sf /run/secrets/identity identity.yaml
RUN ln -sf /run/secrets/nasc-response nasc_response.txt

CMD ["python3", "friendbot.py"]

