/**
 * ConnectionTestStatus — l'esito dell'ultimo «Test connessione» di un
 * elemento di una lista (regola in utils/connectionTestStatus).
 */
import React from 'react';
import { describeConnectionTest } from '../../utils/connectionTestStatus';
import './ConnectionTestStatus.css';

export default function ConnectionTestStatus({ lastTest }) {
  const { tone, label, detail } = describeConnectionTest(lastTest);
  return (
    <span className={`conn-test-status conn-test-status--${tone}`} title={detail}>
      {label}
    </span>
  );
}
