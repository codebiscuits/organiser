// Alpine.js countdown component for deadline tasks
document.addEventListener('alpine:init', () => {
    Alpine.data('countdown', (deadline) => ({
        timeRemaining: '',
        deadlineDate: new Date(deadline),
        
        init() {
            this.updateCountdown();
            setInterval(() => this.updateCountdown(), 1000);
        },
        
        updateCountdown() {
            const now = new Date();
            const diff = this.deadlineDate - now;
            
            if (diff <= 0) {
                this.timeRemaining = 'OVERDUE';
                return;
            }
            
            const days = Math.floor(diff / (1000 * 60 * 60 * 24));
            const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
            const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
            const seconds = Math.floor((diff % (1000 * 60)) / 1000);
            
            if (days > 0) {
                this.timeRemaining = `${days}d ${hours}h`;
            } else if (hours > 0) {
                this.timeRemaining = `${hours}h ${minutes}m`;
            } else {
                this.timeRemaining = `${minutes}m ${seconds}s`;
            }
        }
    }));
});

