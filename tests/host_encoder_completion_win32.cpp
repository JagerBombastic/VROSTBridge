// Compile against the matching ALVR cpp/shared/threadtools.cpp on Windows.
// This exercises real Win32 event semantics used by CEncoder, including a
// capture that returns before scheduling a new encoder job after AcquireSync
// failed. An auto-reset completion event deadlocks the next Present in that case.
#include "shared/threadtools.h"
#include <iostream>
#include <stdexcept>
#include <thread>

static void require(bool value,const char *message) {
    if(!value) throw std::runtime_error(message);
}
int main() {
    try {
        CThreadEvent completion(true); // CEncoder's manual-reset idle condition.
        completion.Set();
        require(completion.Wait(0),"initial encoder idle wait failed");
        // AcquireSync fails here: no NewFrameReady/Reset, no encoder Set follows.
        require(completion.Wait(20),"capture failure consumed encoder completion");
        require(completion.Wait(0),"repeated skipped captures must remain idle");
        completion.Reset(); // Actual NewFrameReady takes ownership of the input.
        require(!completion.Wait(1),"pending encode incorrectly reports idle");
        std::thread encoder([&] { Sleep(10);completion.Set(); });
        const bool finished=completion.Wait(2000);
        encoder.join();
        require(finished,"encoder completion did not wake waiting capture");
        require(completion.Wait(0),"finished encoder must stay idle until next job");
        // Confirm this regression sequence detects the original event type.
        CThreadEvent oldAutoReset(false);
        oldAutoReset.Set();
        require(oldAutoReset.Wait(0),"auto-reset control failed first wait");
        require(!oldAutoReset.Wait(1),"auto-reset control failed to consume signal");
        std::cout<<"Win32 encoder completion: skipped capture, repeated waits, busy state and wakeup passed\n";
        return 0;
    } catch(const std::exception &error) {
        std::cerr<<error.what()<<'\n';return 1;
    }
}
