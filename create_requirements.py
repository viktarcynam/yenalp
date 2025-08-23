def main():
    """Generates the requirements.txt file."""
    requirements = [
        "requests",
        "websocket-client",
        "msgpack"
    ]
    with open("requirements.txt", "w") as f:
        for req in requirements:
            f.write(req + "\n")
    print("Successfully created requirements.txt")

if __name__ == "__main__":
    main()
