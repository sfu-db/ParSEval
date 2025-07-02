{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "FLOAT"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 19,
              "name": "$19",
              "type": "FLOAT"
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
                      "index": 18,
                      "name": "$18",
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
                  "index": 18,
                  "name": "$18",
                  "type": "FLOAT"
                }
              ]
            }
          ]
        },
        {
          "kind": "DIVIDE",
          "operator": "/",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "CAST",
              "operator": "CAST",
              "type": "REAL",
              "operands": [
                {
                  "kind": "INPUT_REF",
                  "index": 19,
                  "name": "$19",
                  "type": "FLOAT"
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
                      "index": 18,
                      "name": "$18",
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
                  "index": 18,
                  "name": "$18",
                  "type": "FLOAT"
                }
              ]
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
                "index": 5,
                "name": "$5",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Alameda",
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
              "table": "frpm",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}