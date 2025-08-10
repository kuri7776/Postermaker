FROM python:3.12
WORKDIR /app

COPY requirements.txt requirements.txt
RUN pip3 install -r requirements.txt

COPY . .
CMD flask run -h 0.0.0.0 -p 8080 & python3 anilist.py
