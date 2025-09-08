{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "DIVIDE",
      "operator": "/",
      "type": "REAL",
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
                  "type": "INTEGER"
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
          "type": "INTEGER",
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
                  "type": "INTEGER"
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            },
            {
              "kind": "LITERAL",
              "value": "NULL",
              "type": "INTEGER",
              "nullable": true,
              "precision": 10
            },
            {
              "kind": "INPUT_REF",
              "index": 1,
              "name": "$1",
              "type": "INTEGER"
            }
          ]
        }
      ]
    }
  ],
  "id": "4",
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
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        },
        {
          "operator": "SUM",
          "distinct": false,
          "ignoreNulls": false,
          "operands": [
            {
              "column": 1,
              "type": "INTEGER"
            }
          ],
          "type": "INTEGER",
          "name": null
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalProject",
          "project": [
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 5,
                      "name": "$5",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "+",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            },
            {
              "kind": "CASE",
              "operator": "CASE",
              "type": "INTEGER",
              "operands": [
                {
                  "kind": "EQUALS",
                  "operator": "=",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "INPUT_REF",
                      "index": 5,
                      "name": "$5",
                      "type": "VARCHAR"
                    },
                    {
                      "kind": "LITERAL",
                      "value": "-",
                      "type": "VARCHAR",
                      "nullable": false,
                      "precision": -1
                    }
                  ]
                },
                {
                  "kind": "LITERAL",
                  "value": 1,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                },
                {
                  "kind": "LITERAL",
                  "value": 0,
                  "type": "INTEGER",
                  "nullable": false,
                  "precision": 10
                }
              ]
            }
          ],
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalFilter",
              "condition": {
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
                    "value": "M",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              "variableset": "[]",
              "id": "1",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
                  "id": "0",
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