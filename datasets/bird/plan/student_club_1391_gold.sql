{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "FLOAT",
      "operands": [
        {
          "kind": "TIMES",
          "operator": "*",
          "type": "REAL",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 0,
                  "name": "$0",
                  "type": "FLOAT"
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": 100,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "CASE",
          "operator": "CASE",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "EQUALS",
              "operator": "=",
              "type": "BOOLEAN",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 1,
                  "name": "$1",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "FLOAT",
                  "nullable": false,
                  "precision": 15
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "FLOAT",
              "nullable": true,
              "precision": 15
            },
            {
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "FLOAT"
            }
          ]
        }
      ]
    }
  ],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalAggregate",
      "keys": [],
      "aggs": [
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 0,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "FLOAT"
            }
          ],
          "type": "FLOAT",
          "name": null
        }
      ],
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "FLOAT",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 1,
                      "name": "$1",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "Yearly Kickoff",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "INPUT_REF",
                  "index": 17,
                  "name": "$17",
                  "type": "FLOAT"
                },
                {
                  "kind": "LITERAL",
                  "value": 0.0,
                  "type": "FLOAT",
                  "nullable": false,
                  "precision": 15
                }
              ]
            },
            {
              "kind": "INPUT_REF",
              "index": 17,
              "name": "$17",
              "type": "FLOAT"
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 20,
                    "name": "$20",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "4",
              "inputs": [
                {
                  "relOp": "LogicalJoin",
                  "joinType": "inner",
                  "condition": {
                    "kind": "EQUALS",
                    "operator": "=",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 0,
                        "name": "$0",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 13,
                        "name": "$13",
                        "type": "VARCHAR"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "event",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "budget",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "expense",
                  "id": "3",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}