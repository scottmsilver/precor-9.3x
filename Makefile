CC = gcc
CFLAGS = -Wall -Wextra -O2 -pthread
LDFLAGS = -lpigpio -lrt -pthread

TARGET = treadmill_io

all: $(TARGET)

$(TARGET): treadmill_io.c
	$(CC) $(CFLAGS) -o $@ $< $(LDFLAGS)

clean:
	rm -f $(TARGET)

.PHONY: all clean
