FROM registry.redhat.io/ubi9/python-311:latest
WORKDIR /opt/app-root/src
COPY app/ ./app/
RUN pip install --no-cache-dir fastapi==0.115.0 "uvicorn[standard]"==0.30.6 httpx==0.27.2 pymongo==4.9.1 segno==1.6.1
EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
